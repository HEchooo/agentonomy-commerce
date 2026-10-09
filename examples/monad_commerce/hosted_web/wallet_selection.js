(function installWalletSelection(root) {
  "use strict";

  const ANNOUNCE_EVENT = "eip6963:announceProvider";
  const REQUEST_EVENT = "eip6963:requestProvider";
  const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;
  const MAX_DESCRIPTOR_LENGTH = 256;

  const isObject = (value) => (
    value !== null && (typeof value === "object" || typeof value === "function")
  );

  const safeDescriptor = (value) => {
    if (typeof value !== "string" || value.length < 1 || value.length > MAX_DESCRIPTOR_LENGTH) {
      return null;
    }
    if (value !== value.trim()) return null;
    for (const character of value) {
      const code = character.codePointAt(0);
      if (code <= 0x1f || (code >= 0x7f && code <= 0x9f)) return null;
    }
    return value;
  };

  const normalizeAddress = (value) => (
    typeof value === "string" && ADDRESS_RE.test(value) ? value.toLowerCase() : null
  );

  const normalizeAccounts = (value) => {
    if (!Array.isArray(value)) throw new Error("Wallet returned an invalid account response");
    const accounts = [];
    const seen = new Set();
    for (const item of value) {
      const address = normalizeAddress(item);
      if (address !== null && !seen.has(address)) {
        seen.add(address);
        accounts.push(address);
      }
    }
    return accounts;
  };

  const parseChainId = (value) => {
    let numeric;
    try {
      if (typeof value === "number") {
        if (!Number.isSafeInteger(value)) throw new Error();
        numeric = BigInt(value);
      } else if (typeof value === "string") {
        if (/^0x[0-9a-fA-F]+$/.test(value) || /^[0-9]+$/.test(value)) {
          numeric = BigInt(value);
        } else {
          throw new Error();
        }
      } else {
        throw new Error();
      }
    } catch (_error) {
      throw new Error("Wallet returned an invalid chain id");
    }
    if (numeric <= 0n || numeric > BigInt(Number.MAX_SAFE_INTEGER)) {
      throw new Error("Wallet returned an invalid chain id");
    }
    return Number(numeric);
  };

  const eventFor = (target, type) => {
    const EventConstructor = typeof target.Event === "function"
      ? target.Event
      : typeof root.Event === "function" ? root.Event : null;
    if (EventConstructor) return new EventConstructor(type);
    return {type};
  };

  const create = (options = {}) => {
    if (!isObject(options)) throw new TypeError("Wallet selection options are required");
    const eventTarget = options.eventTarget || root;
    if (!isObject(eventTarget)
      || typeof eventTarget.addEventListener !== "function"
      || typeof eventTarget.removeEventListener !== "function"
      || typeof eventTarget.dispatchEvent !== "function") {
      throw new TypeError("Wallet selection eventTarget is invalid");
    }
    if (options.onChange !== undefined && typeof options.onChange !== "function") {
      throw new TypeError("Wallet selection onChange must be a function");
    }
    if (options.onError !== undefined && typeof options.onError !== "function") {
      throw new TypeError("Wallet selection onError must be a function");
    }

    const onChange = options.onChange || (() => {});
    const onError = options.onError || (() => {});
    const records = new Map();
    let providerUuids = new WeakMap();
    let announceListenerAttached = false;
    let disposed = false;
    let selectedRecord = null;
    let selectedProvider = null;
    let selectedBindingToken = null;
    let selectedAccounts = [];
    let selectedAccount = null;
    let selectedChainId = null;
    let generation = 0;
    let pendingSelection = null;
    let detachSelectedProvider = () => {};
    const snapshotMetadata = new WeakMap();

    const notifyError = (error) => {
      try {
        onError(error instanceof Error ? error : new Error(String(error)));
      } catch (_callbackError) {
        // Error reporting must not change wallet binding state.
      }
    };

    const operationError = (message, cause = null) => {
      const error = new Error(message);
      if (cause instanceof Error) error.cause = cause;
      notifyError(error);
      return error;
    };

    const notifyChange = () => {
      const value = makeSnapshot();
      try {
        onChange(value);
      } catch (error) {
        notifyError(error);
      }
    };

    const makeSnapshot = () => {
      const value = {
        walletUuid: selectedRecord?.uuid || null,
        walletName: selectedRecord?.name || null,
        accounts: selectedAccounts.slice(),
        account: selectedAccount,
        chainId: selectedChainId,
        connected: Boolean(selectedProvider && selectedAccount && selectedChainId !== null),
        generation,
      };
      snapshotMetadata.set(value, {
        provider: selectedProvider,
        generation,
        account: selectedAccount,
        chainId: selectedChainId,
        walletUuid: value.walletUuid,
        walletName: value.walletName,
        accounts: value.accounts.slice(),
        connected: value.connected,
      });
      return value;
    };

    const ensureLive = () => {
      if (disposed) throw operationError("Wallet selection controller is disposed");
    };

    const detachProvider = () => {
      try {
        detachSelectedProvider();
      } catch (_error) {
        // Provider listener cleanup is best effort after invalidation.
      }
      detachSelectedProvider = () => {};
    };

    const invalidateSelection = () => {
      detachProvider();
      pendingSelection = null;
      selectedRecord = null;
      selectedProvider = null;
      selectedBindingToken = null;
      selectedAccounts = [];
      selectedAccount = null;
      selectedChainId = null;
      generation += 1;
      notifyChange();
    };

    const attachProviderListeners = (record, bindingToken, pending) => {
      const provider = record.provider;
      const add = typeof provider.on === "function"
        ? provider.on.bind(provider)
        : typeof provider.addListener === "function"
          ? provider.addListener.bind(provider)
          : null;
      const remove = typeof provider.removeListener === "function"
        ? provider.removeListener.bind(provider)
        : typeof provider.removeEventListener === "function"
          ? provider.removeEventListener.bind(provider)
          : null;
      if (!add || !remove) {
        throw new Error("Wallet provider event listeners are unavailable");
      }
      const listeners = new Map();
      let attached = true;
      const isCurrentBinding = () => (
        !disposed
        && (
          (selectedProvider === provider && selectedBindingToken === bindingToken)
          || (pendingSelection === pending && generation === pending.generation)
        )
      );
      const detach = () => {
        if (!attached) return;
        attached = false;
        for (const [eventName, listener] of listeners) {
          try { remove(eventName, listener); } catch (_error) { /* best effort */ }
        }
      };
      const invalidateIfCurrent = () => {
        if (!isCurrentBinding()) return;
        invalidateSelection();
      };
      detachSelectedProvider = detach;
      try {
        for (const eventName of ["accountsChanged", "chainChanged", "disconnect"]) {
          const listener = () => invalidateIfCurrent();
          listeners.set(eventName, listener);
          add(eventName, listener);
          if (!isCurrentBinding()) {
            detach();
            if (detachSelectedProvider === detach) detachSelectedProvider = () => {};
            return;
          }
        }
      } catch (error) {
        detach();
        if (detachSelectedProvider === detach) detachSelectedProvider = () => {};
        throw error;
      }
    };

    const providerRequest = (provider, method) => {
      let request;
      try { request = provider.request; } catch (_error) { request = null; }
      if (typeof request !== "function") {
        throw new Error("Wallet provider request is unavailable");
      }
      return request.call(provider, {method});
    };

    const isCurrentPending = (pending) => (
      !disposed && pendingSelection === pending && generation === pending.generation
    );

    const registerProvider = (detail) => {
      if (!isObject(detail) || !isObject(detail.info) || !isObject(detail.provider)) return false;
      let request;
      try { request = detail.provider.request; } catch (_error) { request = null; }
      if (typeof request !== "function") return false;
      const uuid = safeDescriptor(detail.info.uuid);
      const name = safeDescriptor(detail.info.name);
      const rdns = safeDescriptor(detail.info.rdns);
      if (!uuid || !name || !rdns) return false;
      if (records.has(uuid) || providerUuids.has(detail.provider)) return false;
      const record = Object.freeze({uuid, name, rdns, provider: detail.provider});
      records.set(uuid, record);
      providerUuids.set(detail.provider, uuid);
      notifyChange();
      return true;
    };

    const announceProvider = (event) => {
      try { registerProvider(event?.detail); } catch (error) { notifyError(error); }
    };

    const ensureAnnouncementListener = () => {
      if (announceListenerAttached) return;
      eventTarget.addEventListener(ANNOUNCE_EVENT, announceProvider);
      announceListenerAttached = true;
    };

    const discover = () => {
      ensureLive();
      ensureAnnouncementListener();
      eventTarget.dispatchEvent(eventFor(eventTarget, REQUEST_EVENT));
      return wallets();
    };

    const wallets = () => Array.from(records.values(), (record) => ({
      uuid: record.uuid,
      name: record.name,
      rdns: record.rdns,
    }));

    const selectProvider = async (uuid) => {
      ensureLive();
      const record = records.get(uuid);
      if (!record) throw operationError("Unknown wallet provider");
      invalidateSelection();
      const pending = {record, generation, bindingToken: {}};
      pendingSelection = pending;
      try {
        attachProviderListeners(record, pending.bindingToken, pending);
        if (!isCurrentPending(pending)) return makeSnapshot();
        const rawAccounts = await providerRequest(record.provider, "eth_requestAccounts");
        if (!isCurrentPending(pending)) return makeSnapshot();
        const accounts = normalizeAccounts(rawAccounts);
        const rawChainId = await providerRequest(record.provider, "eth_chainId");
        if (!isCurrentPending(pending)) return makeSnapshot();
        const chainId = parseChainId(rawChainId);
        selectedRecord = record;
        selectedProvider = record.provider;
        selectedBindingToken = pending.bindingToken;
        selectedAccounts = accounts;
        selectedAccount = null;
        selectedChainId = chainId;
        pendingSelection = null;
        notifyChange();
        return makeSnapshot();
      } catch (cause) {
        if (!isCurrentPending(pending)) return makeSnapshot();
        invalidateSelection();
        throw operationError("Wallet provider selection failed", cause);
      }
    };

    const selectAccount = (address) => {
      ensureLive();
      const normalized = normalizeAddress(address);
      if (!normalized) throw operationError("Wallet account address is invalid");
      if (!selectedProvider || !selectedAccounts.includes(normalized)) {
        throw operationError("Choose an account returned by the selected wallet");
      }
      selectedAccount = normalized;
      generation += 1;
      notifyChange();
      return makeSnapshot();
    };

    const assertSnapshot = (value) => {
      ensureLive();
      const metadata = snapshotMetadata.get(value);
      const accountsMatch = Boolean(
        metadata
        && Array.isArray(value?.accounts)
        && value.accounts.length === metadata.accounts.length
        && value.accounts.every((account, index) => account === metadata.accounts[index])
      );
      if (!metadata
        || metadata.provider !== selectedProvider
        || metadata.generation !== generation
        || metadata.account !== selectedAccount
        || metadata.chainId !== selectedChainId
        || value.walletUuid !== (selectedRecord?.uuid || null)) {
        throw operationError("Wallet state changed; choose the wallet and account again");
      }
      if (
        !accountsMatch
        || value.walletName !== (selectedRecord?.name || null)
        || value.connected !== Boolean(selectedProvider && selectedAccount && selectedChainId !== null)
        || value.walletName !== metadata.walletName
        || value.connected !== metadata.connected
        || value.generation !== metadata.generation
        || value.account !== metadata.account
        || value.chainId !== metadata.chainId
        || value.walletUuid !== metadata.walletUuid
      ) {
        throw operationError("Wallet state changed; choose the wallet and account again");
      }
      return true;
    };

    const clear = () => {
      ensureLive();
      invalidateSelection();
      return makeSnapshot();
    };

    const dispose = () => {
      if (disposed) return;
      disposed = true;
      if (announceListenerAttached) {
        try { eventTarget.removeEventListener(ANNOUNCE_EVENT, announceProvider); } catch (_error) { /* best effort */ }
        announceListenerAttached = false;
      }
      detachProvider();
      pendingSelection = null;
      selectedRecord = null;
      selectedProvider = null;
      selectedBindingToken = null;
      selectedAccounts = [];
      selectedAccount = null;
      selectedChainId = null;
      generation += 1;
      records.clear();
      providerUuids = new WeakMap();
    };

    return Object.freeze({
      discover,
      wallets,
      selectProvider,
      selectAccount,
      snapshot: makeSnapshot,
      provider: () => selectedProvider,
      assertSnapshot,
      clear,
      dispose,
    });
  };

  root.AgentonomyWalletSelection = Object.freeze({create});
})(globalThis);
