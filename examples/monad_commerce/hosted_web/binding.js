(function installAgentonomyBinding(root) {
  "use strict";

  const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;
  const SIGNATURE_RE = /^0x[0-9a-fA-F]{130}$/;
  const HASH_RE = /^0x[0-9a-fA-F]{64}$/;
  const OPAQUE_REQUEST_RE = /^[A-Za-z0-9_-]{43}$/;
  const BYTES32_RE = /^0x[0-9a-fA-F]{64}$/;
  const HEX_RE = /^0x[0-9a-fA-F]+$/;
  const DECIMAL_RE = /^[0-9]+$/;
  const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);
  const CLAIM_SELECTOR = "0x4e71d92d";
  const APPROVE_SELECTOR = "0x095ea7b3";
  const REVOKE_SELECTOR = "0xb75c7dc6";

  const isObject = (value) => value !== null && typeof value === "object";
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const normalizeAddress = (value) => (
    typeof value === "string" && ADDRESS_RE.test(value) ? value.toLowerCase() : null
  );
  const parseInteger = (value, label) => {
    let result;
    try {
      if (typeof value === "number" && Number.isSafeInteger(value)) result = BigInt(value);
      else if (typeof value === "string" && (HEX_RE.test(value) || DECIMAL_RE.test(value))) result = BigInt(value);
      else throw new Error();
    } catch (_error) {
      throw new Error(label + " is invalid");
    }
    if (result < 0n || result > MAX_SAFE) throw new Error(label + " is outside the safe range");
    return Number(result);
  };
  const parseAtomic = (value, label) => {
    if (typeof value !== "string" || !DECIMAL_RE.test(value) || value.length > 78) {
      throw new Error(label + " is invalid");
    }
    const result = BigInt(value);
    if (result < 0n) throw new Error(label + " is invalid");
    return result;
  };
  const parseQuantity = (value, label) => {
    if (typeof value !== "string" || !(HEX_RE.test(value) || DECIMAL_RE.test(value))) {
      throw new Error(label + " is invalid");
    }
    const result = BigInt(value);
    if (result < 0n) throw new Error(label + " is invalid");
    return result;
  };
  const freezeDeep = (value) => {
    if (!isObject(value) || Object.isFrozen(value)) return value;
    for (const child of Object.values(value)) freezeDeep(child);
    return Object.freeze(value);
  };
  const clone = (value) => {
    if (value === undefined) return undefined;
    return JSON.parse(JSON.stringify(value));
  };
  const safeText = (value, label, max = 512) => {
    if (typeof value !== "string" || value.length < 1 || value.length > max || value !== value.trim()) {
      throw new Error(label + " is invalid");
    }
    for (const character of value) {
      const code = character.codePointAt(0);
      if (code <= 0x1f || (code >= 0x7f && code <= 0x9f)) throw new Error(label + " is invalid");
    }
    return value;
  };
  const safeSigningMessage = (value, label, max = 4096) => {
    if (typeof value !== "string" || value.length < 1 || value.length > max) {
      throw new Error(label + " is invalid");
    }
    for (const character of value) {
      const code = character.codePointAt(0);
      if (code !== 0x0a && (code <= 0x1f || (code >= 0x7f && code <= 0x9f))) {
        throw new Error(label + " is invalid");
      }
    }
    return value;
  };
  const assertExactKeys = (value, keys, label) => {
    if (!isObject(value) || Object.keys(value).some((key) => !keys.includes(key))) {
      throw new Error(label + " has unexpected fields");
    }
    for (const key of keys) if (!own(value, key)) throw new Error(label + " is missing " + key);
  };
  const errorWithData = (message, status, data) => {
    const error = new Error(message);
    error.status = status;
    error.data = data;
    return error;
  };

  const validateNetwork = (network) => {
    assertExactKeys(network, [
      "chain_id", "network", "token", "executor", "payee", "token_symbol", "token_decimals",
      "test_asset", "terms", "wallet_transaction", "budget_domain",
    ], "network");
    const chainId = parseInteger(network.chain_id, "network chain id");
    if (chainId <= 0) throw new Error("network chain id is invalid");
    const token = normalizeAddress(network.token);
    const executor = normalizeAddress(network.executor);
    const payee = normalizeAddress(network.payee);
    if (!token || !executor || !payee) throw new Error("network contract address is invalid");
    safeText(network.network, "network name", 128);
    safeText(network.token_symbol, "token symbol", 32);
    if (!Number.isInteger(network.token_decimals) || network.token_decimals < 0 || network.token_decimals > 36) {
      throw new Error("token decimals are invalid");
    }
    if (network.test_asset !== true) throw new Error("configured asset must be marked as test asset");
    assertExactKeys(network.terms, ["claim_atomic", "total_atomic", "per_payment_atomic", "validity_seconds"], "network terms");
    const claim = parseAtomic(network.terms.claim_atomic, "claim amount");
    const total = parseAtomic(network.terms.total_atomic, "total amount");
    const perPayment = parseAtomic(network.terms.per_payment_atomic, "per-payment amount");
    const validity = parseInteger(network.terms.validity_seconds, "grant validity");
    if (claim <= 0n || total <= 0n || perPayment <= 0n || perPayment > total || validity <= 0) {
      throw new Error("network terms are invalid");
    }
    assertExactKeys(network.wallet_transaction, ["max_gas", "max_gas_price_wei"], "wallet transaction limits");
    if (parseQuantity(network.wallet_transaction.max_gas, "maximum gas") <= 0n
      || parseQuantity(network.wallet_transaction.max_gas_price_wei, "maximum gas price") <= 0n) {
      throw new Error("wallet transaction limits are invalid");
    }
    assertExactKeys(network.budget_domain, ["name", "version"], "budget domain");
    safeText(network.budget_domain.name, "budget domain name", 128);
    safeText(network.budget_domain.version, "budget domain version", 64);
    return {
      ...network,
      chain_id: chainId,
      token,
      executor,
      payee,
      terms: {...network.terms, validity_seconds: validity},
    };
  };

  const validateConfig = (value) => {
    assertExactKeys(value, ["networks"], "wallet configuration");
    if (!Array.isArray(value.networks) || value.networks.length < 1 || value.networks.length > 32) {
      throw new Error("wallet configuration networks are invalid");
    }
    const networks = value.networks.map(validateNetwork);
    const seen = new Set();
    for (const network of networks) {
      if (seen.has(network.chain_id)) throw new Error("duplicate configured chain");
      seen.add(network.chain_id);
    }
    return freezeDeep({networks});
  };

  const eventTargetFor = (options) => options.eventTarget || root;

  const create = (options = {}) => {
    if (!isObject(options)) throw new TypeError("binding options are required");
    const document = options.document || root.document || null;
    const eventTarget = eventTargetFor(options);
    const fetchImpl = options.fetch || root.fetch;
    if (typeof fetchImpl !== "function") throw new TypeError("binding fetch is unavailable");
    if (!isObject(eventTarget)) throw new TypeError("binding event target is invalid");
    if (!root.AgentonomyWalletSelection || typeof root.AgentonomyWalletSelection.create !== "function") {
      throw new Error("EIP-6963 wallet selection is unavailable");
    }

    const location = options.location || root.location || {pathname: ""};
    const requestToken = options.requestToken || String(location.pathname || "").split("/").pop() || null;
    if (requestToken !== null && requestToken !== "" && !OPAQUE_REQUEST_RE.test(requestToken)) {
      throw new Error("pairing request is invalid");
    }
    const pollIntervalMs = Number.isFinite(options.pollIntervalMs) && options.pollIntervalMs > 0
      ? Math.max(1, Math.floor(options.pollIntervalMs)) : 2000;

    let config = null;
    let selectedNetwork = null;
    let walletSnapshot = null;
    let previousWalletSnapshot = null;
    let walletController = null;
    let disposed = false;
    let generation = 0;
    let operationNumber = 0;
    let activeOperation = null;
    const recoveredOperations = new Map();
    let csrfToken = null;
    let sessionKnown = false;
    let sessionPromise = null;
    let cookieWriteTail = Promise.resolve();
    let deviceTimer = null;
    let domBound = false;

    const state = {
      wallets: [], accounts: [], walletUuid: null, walletName: null, account: null, chainId: null,
      connected: false, authenticated: false, owner: null, status: null, phase: null,
      device: null, deviceChallenge: null, claimHash: null, allowanceHash: null, revokeHash: null,
      claimVerification: null, allowanceVerification: null, revokeVerification: null,
      message: "Choose a wallet provider and account.", busy: false, devices: [], recoveryConflicts: {},
    };

    const element = (id) => document && typeof document.getElementById === "function"
      ? document.getElementById(id) : null;
    const setText = (id, value) => {
      const node = element(id);
      if (node) node.textContent = value == null ? "" : String(value);
    };
    const setDisabled = (id, disabled) => {
      const node = element(id);
      if (node) node.disabled = Boolean(disabled);
    };
    const showMessage = (value) => {
      state.message = String(value);
      setText("message", state.message);
    };

    const recoveryStorageKey = (kind, owner, chainId) => (
      "agentonomy.binding.tx." + kind + "." + owner + "." + String(chainId)
    );
    const rememberRecoveredHash = (kind, hash, owner, chainId) => {
      const normalizedOwner = normalizeAddress(owner);
      if (!normalizedOwner || !Number.isSafeInteger(chainId) || chainId <= 0) return;
      const record = {kind, hash, owner: normalizedOwner, chainId};
      const scopedKey = recoveryStorageKey(kind, normalizedOwner, chainId);
      recoveredOperations.set(scopedKey, record);
      try {
        if (root.sessionStorage && typeof root.sessionStorage.setItem === "function") {
          root.sessionStorage.setItem(recoveryStorageKey(kind, normalizedOwner, chainId), JSON.stringify(record));
        }
      } catch (_error) {
        // Browser storage can be disabled; in-memory recovery remains available.
      }
    };
    const loadRecoveredHash = (kind, owner, chainId) => {
      const normalizedOwner = normalizeAddress(owner);
      if (!normalizedOwner || !Number.isSafeInteger(chainId) || chainId <= 0) return null;
      const scopedKey = recoveryStorageKey(kind, normalizedOwner, chainId);
      if (recoveredOperations.has(scopedKey)) return recoveredOperations.get(scopedKey);
      try {
        if (root.sessionStorage && typeof root.sessionStorage.getItem === "function") {
          const raw = root.sessionStorage.getItem(scopedKey);
          if (raw) {
            const record = JSON.parse(raw);
            if (isObject(record) && record.kind === kind && HASH_RE.test(record.hash)
              && normalizeAddress(record.owner) === normalizedOwner
              && record.chainId === chainId) {
              const normalized = {
                kind, hash: record.hash.toLowerCase(), owner: normalizedOwner, chainId,
              };
              recoveredOperations.set(scopedKey, normalized);
              return normalized;
            }
          }
        }
      } catch (_error) {
        // Ignore malformed or inaccessible browser storage.
      }
      return null;
    };
    const recoveryKinds = [["claim", "claimHash"], ["approval", "allowanceHash"], ["revocation", "revokeHash"]];
    const recoverSentHashes = (owner, chainId) => {
      for (const [kind, stateKey] of recoveryKinds) {
        const record = loadRecoveredHash(kind, owner, chainId);
        if (record && record.owner === owner && record.chainId === chainId && !state[stateKey]) {
          state[stateKey] = record.hash;
        }
      }
      render();
    };
    const clearRecoveryInputs = () => {
      for (const id of ["claim-hash", "allowance-hash", "revoke-hash"]) {
        const node = element(id);
        if (node) node.value = "";
      }
    };
    const markRecoveryConflict = (stateKey, localHash, serverHash) => {
      state.recoveryConflicts[stateKey] = {local: localHash, server: serverHash};
      showMessage("Hosted transaction recovery conflict; verify the recorded hash before continuing.");
    };
    const clearRecoveryConflict = (stateKey) => {
      delete state.recoveryConflicts[stateKey];
    };

    const selectedNetworkValue = () => {
      if (!config || selectedNetwork === null) return null;
      return config.networks.find((network) => network.chain_id === selectedNetwork) || null;
    };

    const publicState = () => ({
      wallets: state.wallets.map((wallet) => ({...wallet})),
      accounts: state.accounts.slice(), walletUuid: state.walletUuid, walletName: state.walletName,
      account: state.account, chainId: state.chainId, connected: state.connected,
      network: clone(selectedNetworkValue()), networks: clone(config ? config.networks : []),
      authenticated: state.authenticated, owner: state.owner, status: clone(state.status),
      devices: clone(state.devices),
      phase: state.phase, device: clone(state.device), deviceChallenge: clone(state.deviceChallenge),
      claimHash: state.claimHash, allowanceHash: state.allowanceHash, revokeHash: state.revokeHash,
      claimVerification: state.claimVerification, allowanceVerification: state.allowanceVerification,
      revokeVerification: state.revokeVerification, recoveryConflicts: clone(state.recoveryConflicts),
      message: state.message, generation,
      busy: state.busy,
    });

    const assertCurrent = (operation) => {
      if (disposed || !operation || operation.generation !== generation) {
        throw new Error("Wallet state changed; choose the wallet and account again");
      }
    };
    const beginOperation = () => {
      if (disposed) throw new Error("binding is disposed");
      if (activeOperation) throw new Error("Another wallet action is already in progress");
      const operation = {generation, id: ++operationNumber};
      activeOperation = operation;
      state.busy = true;
      return operation;
    };
    const finishOperation = (operation) => {
      if (activeOperation === operation) {
        activeOperation = null;
        state.busy = false;
        render();
      }
    };
    const run = async (work) => {
      const operation = beginOperation();
      try { return await work(operation); } finally { finishOperation(operation); }
    };

    const queueCookieWrite = (callback) => {
      cookieWriteTail = cookieWriteTail.then(callback, callback).catch(() => undefined);
      return cookieWriteTail;
    };

    const clearOwnerState = () => {
      state.authenticated = false;
      state.owner = null;
      state.status = null;
      state.phase = null;
      state.devices = [];
      state.claimHash = null;
      state.allowanceHash = null;
      state.revokeHash = null;
      state.claimVerification = null;
      state.allowanceVerification = null;
      state.revokeVerification = null;
      state.recoveryConflicts = {};
      state.device = null;
      state.deviceChallenge = null;
      if (deviceTimer !== null) {
        clearTimeout(deviceTimer);
        deviceTimer = null;
      }
      clearRecoveryInputs();
    };

    const invalidateSession = () => {
      const oldCsrf = csrfToken;
      generation += 1;
      sessionKnown = false;
      sessionPromise = null;
      csrfToken = null;
      clearOwnerState();
      if (oldCsrf) {
        queueCookieWrite(async () => {
          try {
            await fetchImpl("/api/logout", {
              method: "POST", credentials: "same-origin",
              headers: {"Content-Type": "application/json", "X-Agentonomy-CSRF": oldCsrf},
              body: "{}",
            });
          } catch (_error) {
            // A changed wallet must not be blocked by cleanup of the old session.
          }
        });
      }
    };

    const handleWalletSnapshot = (snapshot) => {
      if (disposed) return;
      previousWalletSnapshot = walletSnapshot;
      walletSnapshot = snapshot;
      state.wallets = walletController ? walletController.wallets() : [];
      state.accounts = snapshot.accounts.slice();
      state.walletUuid = snapshot.walletUuid;
      state.walletName = snapshot.walletName;
      state.account = snapshot.account;
      state.chainId = snapshot.chainId;
      state.connected = snapshot.connected;
      const old = previousWalletSnapshot;
      const walletChanged = Boolean(old && old.walletUuid && (
        !snapshot.walletUuid || old.walletUuid !== snapshot.walletUuid
      ));
      const accountChanged = Boolean(old && old.account && old.account !== snapshot.account);
      const chainChanged = Boolean(old && old.chainId !== null && old.chainId !== snapshot.chainId);
      if (walletChanged || accountChanged || chainChanged) invalidateSession();
      render();
    };

    const walletError = (error) => showMessage(error instanceof Error ? error.message : String(error));

    const discover = () => {
      const wallets = walletController.discover();
      state.wallets = walletController.wallets();
      render();
      return wallets;
    };

    const selectProvider = async (uuid) => {
      const result = await walletController.selectProvider(uuid);
      render();
      return result;
    };

    const selectAccount = (address) => {
      const result = walletController.selectAccount(address);
      render();
      return result;
    };

    const selectNetwork = (chainId) => {
      const parsed = parseInteger(chainId, "selected chain id");
      if (!config || !config.networks.some((network) => network.chain_id === parsed)) {
        throw new Error("Choose a configured network");
      }
      selectedNetwork = parsed;
      showMessage("Configured network selected; verify the wallet chain before signing.");
      render();
      return selectedNetworkValue();
    };

    const request = async (path, options = {}, operation = null) => {
      if (operation) assertCurrent(operation);
      const method = options.method || "GET";
      const headers = {...(options.headers || {})};
      let body;
      if (options.body !== undefined) {
        body = typeof options.body === "string" ? options.body : JSON.stringify(options.body);
        headers["Content-Type"] = "application/json";
      }
      if (method !== "GET" && path !== "/api/session" && csrfToken) headers["X-Agentonomy-CSRF"] = csrfToken;
      const result = await fetchImpl(path, {
        method, headers, body, credentials: "same-origin",
      });
      if (operation) assertCurrent(operation);
      let payload = null;
      try { payload = await result.json(); } catch (_error) { payload = {}; }
      if (!result.ok) throw errorWithData("Hosted request failed", result.status, payload);
      return payload;
    };

    const ensureSession = async (operation = null) => {
      if (sessionKnown && csrfToken) return;
      if (!sessionPromise) {
        const sessionGeneration = generation;
        const pending = cookieWriteTail.then(() => request("/api/session", {method: "POST", body: {}}, operation))
          .then((payload) => {
            if (sessionGeneration !== generation) throw new Error("Wallet state changed; choose the wallet and account again");
            if (!payload || typeof payload.csrf_token !== "string" || payload.csrf_token.length < 1) {
              throw new Error("Hosted session did not return a CSRF token");
            }
            csrfToken = payload.csrf_token;
            sessionKnown = true;
            state.authenticated = payload.authenticated === true;
            state.owner = normalizeAddress(payload.owner);
            return payload;
          })
          .finally(() => { if (sessionPromise === pending) sessionPromise = null; });
        sessionPromise = pending;
      }
      const payload = await sessionPromise;
      if (operation) assertCurrent(operation);
      return payload;
    };

    const refreshStatusInternal = async (operation) => {
      await ensureSession(operation);
      const payload = await request("/api/status", {}, operation);
      if (payload && typeof payload === "object") {
        state.status = payload;
        state.authenticated = payload.authenticated === true;
        state.owner = normalizeAddress(payload.owner) || (state.authenticated ? state.account : null);
        state.phase = payload.onboarding && payload.onboarding.phase ? payload.onboarding.phase : null;
        const listedDevices = Array.isArray(payload.devices)
          ? payload.devices : (payload.opc && Array.isArray(payload.opc.devices) ? payload.opc.devices : []);
        state.devices = listedDevices.filter((device) => isObject(device)).map((device) => ({
          label: typeof device.label === "string" ? device.label : "Unnamed device",
          status: typeof device.status === "string" ? device.status : "unknown",
          installation_id: typeof device.installation_id === "string" ? device.installation_id : null,
        }));
        const operations = isObject(payload.wallet_operations) ? payload.wallet_operations : {};
        for (const [operationName, stateKey, verificationKey] of [
          ["claim", "claimHash", "claimVerification"],
          ["approval", "allowanceHash", "allowanceVerification"],
          ["revocation", "revokeHash", "revokeVerification"],
        ]) {
          const record = isObject(operations[operationName]) ? operations[operationName] : null;
          if (!record || typeof record.transaction_hash !== "string") continue;
          const hash = validateHash(record.transaction_hash);
          const scopeOwner = normalizeAddress(state.account);
          const scopeChain = state.chainId || (selectedNetworkValue() && selectedNetworkValue().chain_id);
          const localRecord = scopeOwner && scopeChain
            ? loadRecoveredHash(operationName, scopeOwner, scopeChain) : null;
          const localHash = (localRecord && localRecord.hash) || state[stateKey];
          if (localHash && localHash !== hash) {
            state[stateKey] = localHash;
            markRecoveryConflict(stateKey, localHash, hash);
            continue;
          }
          clearRecoveryConflict(stateKey);
          state[stateKey] = hash;
          if (typeof record.status === "string") state[verificationKey] = record.status;
        }
      }
      render();
      return payload;
    };

    const refreshStatus = () => run(refreshStatusInternal);

    const parseChallenge = (payload, label) => {
      if (!isObject(payload)) throw new Error(label + " is invalid");
      const challengeId = payload.challenge_id || payload.session_id;
      safeText(challengeId, label + " id", 256);
      safeSigningMessage(payload.message_to_sign, label + " message", 4096);
      return {id: challengeId, message: payload.message_to_sign};
    };

    const assertConfiguredWallet = async (operation) => {
      assertCurrent(operation);
      const network = selectedNetworkValue();
      if (!network) throw new Error("Choose a configured network");
      if (!walletSnapshot || !walletSnapshot.account || !walletSnapshot.walletUuid) {
        throw new Error("Choose a wallet provider and account");
      }
      walletController.assertSnapshot(walletSnapshot);
      const provider = walletController.provider();
      if (!provider || typeof provider.request !== "function") throw new Error("Selected wallet is unavailable");
      const rawChain = await provider.request({method: "eth_chainId"});
      assertCurrent(operation);
      const chainId = parseInteger(rawChain, "wallet chain id");
      const currentSnapshot = walletController.snapshot();
      walletController.assertSnapshot(currentSnapshot);
      if (currentSnapshot.account !== state.account || chainId !== network.chain_id) {
        throw new Error("Wallet network does not match the configured network");
      }
      state.chainId = chainId;
      return {network, provider, account: currentSnapshot.account};
    };

    const utf8Hex = (value) => {
      const bytes = new TextEncoder().encode(String(value));
      return "0x" + Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    };
    const signPersonal = async (message, operation) => {
      safeSigningMessage(message, "wallet signing message", 4096);
      const before = await assertConfiguredWallet(operation);
      const signature = await before.provider.request({
        method: "personal_sign", params: [utf8Hex(message), before.account],
      });
      assertCurrent(operation);
      if (typeof signature !== "string" || !SIGNATURE_RE.test(signature)) throw new Error("Wallet returned an invalid signature");
      await assertConfiguredWallet(operation);
      return signature;
    };
    const signTyped = async (typedData, operation) => {
      const before = await assertConfiguredWallet(operation);
      const signature = await before.provider.request({
        method: "eth_signTypedData_v4", params: [before.account, JSON.stringify(typedData)],
      });
      assertCurrent(operation);
      if (typeof signature !== "string" || !SIGNATURE_RE.test(signature)) throw new Error("Wallet returned an invalid signature");
      await assertConfiguredWallet(operation);
      return signature;
    };

    const parseSignaturePayload = (payload, label) => {
      if (!isObject(payload) || typeof payload.signature !== "string" || !SIGNATURE_RE.test(payload.signature)) {
        throw new Error(label + " signature is invalid");
      }
      return payload.signature;
    };

    const login = () => run(async (operation) => {
      const before = await assertConfiguredWallet(operation);
      await ensureSession(operation);
      const challengePayload = await request("/api/login/challenge", {
        method: "POST", body: {owner: before.account},
      }, operation);
      const challenge = parseChallenge(challengePayload, "login challenge");
      const signature = await signPersonal(challenge.message, operation);
      const verified = await request("/api/login/verify", {
        method: "POST", body: {challenge_id: challenge.id, signature},
      }, operation);
      assertCurrent(operation);
      if (!verified || verified.authenticated !== true || (verified.owner && normalizeAddress(verified.owner) !== before.account)) {
        throw new Error("Wallet login did not verify the selected account");
      }
      state.authenticated = true;
      state.owner = before.account;
      await refreshStatusInternal(operation);
      recoverSentHashes(before.account, before.network.chain_id);
      if (Object.keys(state.recoveryConflicts).length === 0) showMessage("Wallet login verified.");
      return publicState();
    });

    const grant = () => run(async (operation) => {
      const before = await assertConfiguredWallet(operation);
      if (!state.authenticated || state.owner !== before.account) throw new Error("Wallet login is required first");
      await ensureSession(operation);
      const challengePayload = await request("/api/grant/challenge", {method: "POST", body: {}}, operation);
      const challenge = parseChallenge(challengePayload, "grant challenge");
      const signature = await signPersonal(challenge.message, operation);
      const verified = await request("/api/grant/verify", {method: "POST", body: {signature}}, operation);
      state.phase = verified && verified.status ? verified.status : "grant verified";
      await refreshStatusInternal(operation);
      showMessage("Spending grant verified.");
      return verified;
    });

    const canonicalUint = (value, label) => {
      if (typeof value === "number" && !Number.isSafeInteger(value)) throw new Error(label + " is not safely representable");
      return parseAtomic(String(value), label).toString();
    };
    const sameTypedSchema = (actual, expected, label) => {
      if (!Array.isArray(actual) || actual.length !== expected.length) throw new Error(label + " is invalid");
      for (let index = 0; index < expected.length; index += 1) {
        const item = actual[index];
        const target = expected[index];
        if (!isObject(item) || Object.keys(item).length !== 2 || item.name !== target.name || item.type !== target.type) {
          throw new Error(label + " is invalid");
        }
      }
    };
    const timestampSeconds = (value, label) => {
      if (typeof value !== "string") throw new Error(label + " is invalid");
      const milliseconds = Date.parse(value);
      if (!Number.isFinite(milliseconds)) throw new Error(label + " is invalid");
      return String(Math.floor(milliseconds / 1000));
    };
    const validateTypedData = (payload, network, account, status = null) => {
      if (!isObject(payload) || !isObject(payload.typed_data) || !isObject(payload.typed_data.domain)
        || !isObject(payload.typed_data.message)) throw new Error("budget typed data is invalid");
      const typed = payload.typed_data;
      assertExactKeys(typed, ["types", "primaryType", "domain", "message"], "budget typed data");
      if (!isObject(payload.grant)) throw new Error("budget grant mirror is missing");
      if (payload.digest !== undefined && (typeof payload.digest !== "string" || !HASH_RE.test(payload.digest))) {
        throw new Error("budget digest is invalid");
      }
      const domain = typed.domain;
      assertExactKeys(domain, ["name", "version", "chainId", "verifyingContract"], "budget domain");
      if (typed.primaryType !== "SpendGrant") throw new Error("budget primary type is invalid");
      assertExactKeys(typed.types, ["EIP712Domain", "SpendGrant"], "budget types");
      sameTypedSchema(typed.types.EIP712Domain, [
        {name: "name", type: "string"}, {name: "version", type: "string"},
        {name: "chainId", type: "uint256"}, {name: "verifyingContract", type: "address"},
      ], "budget domain schema");
      sameTypedSchema(typed.types.SpendGrant, [
        {name: "grantId", type: "bytes32"}, {name: "owner", type: "address"},
        {name: "agentScope", type: "bytes32"}, {name: "token", type: "address"},
        {name: "payee", type: "address"}, {name: "maxPerPayment", type: "uint256"},
        {name: "maxTotal", type: "uint256"}, {name: "validAfter", type: "uint256"},
        {name: "validUntil", type: "uint256"}, {name: "executionSigner", type: "address"},
      ], "budget grant schema");
      if (normalizeAddress(domain.verifyingContract) !== network.executor
        || parseInteger(domain.chainId, "budget chain id") !== network.chain_id
        || domain.name !== network.budget_domain.name || domain.version !== network.budget_domain.version) {
        throw new Error("budget typed data does not match the configured network");
      }
      const message = typed.message;
      assertExactKeys(message, [
        "grantId", "owner", "agentScope", "token", "payee", "maxPerPayment", "maxTotal",
        "validAfter", "validUntil", "executionSigner",
      ], "budget message");
      if (normalizeAddress(message.owner) !== account || normalizeAddress(message.token) !== network.token
        || normalizeAddress(message.payee) !== network.payee
        || canonicalUint(message.maxTotal, "budget total") !== network.terms.total_atomic
        || canonicalUint(message.maxPerPayment, "budget per-payment") !== network.terms.per_payment_atomic) {
        throw new Error("budget terms do not match the configured network");
      }
      if (!BYTES32_RE.test(String(message.grantId || "")) || !BYTES32_RE.test(String(message.agentScope || ""))) {
        throw new Error("budget grant identifiers are invalid");
      }
      const expectedSigner = status && status.onboarding && normalizeAddress(status.onboarding.execution_signer);
      if (!normalizeAddress(message.executionSigner)
        || (expectedSigner && normalizeAddress(message.executionSigner) !== expectedSigner)) {
        throw new Error("budget execution signer is invalid");
      }
      const validAfter = canonicalUint(message.validAfter, "budget start");
      const validUntil = canonicalUint(message.validUntil, "budget expiry");
      const duration = BigInt(validUntil) - BigInt(validAfter);
      const onboarding = status && status.onboarding ? status.onboarding : {};
      const expectedDuration = onboarding.grant_starts_at !== undefined && onboarding.grant_expires_at !== undefined
        ? BigInt(timestampSeconds(onboarding.grant_expires_at, "budget grant expiry"))
          - BigInt(timestampSeconds(onboarding.grant_starts_at, "budget grant start"))
        : BigInt(network.terms.validity_seconds);
      if (BigInt(validUntil) <= BigInt(validAfter) || duration !== expectedDuration) {
        throw new Error("budget validity does not match configured terms");
      }
      if (onboarding.grant_starts_at !== undefined
        && validAfter !== timestampSeconds(onboarding.grant_starts_at, "budget grant start")) {
        throw new Error("budget start does not match the active grant");
      }
      if (onboarding.grant_expires_at !== undefined
        && validUntil !== timestampSeconds(onboarding.grant_expires_at, "budget grant expiry")) {
        throw new Error("budget expiry does not match the active grant");
      }
      assertExactKeys(payload.grant, [
        "grantId", "owner", "agentScope", "token", "payee", "maxPerPayment", "maxTotal",
        "validAfter", "validUntil", "executionSigner",
      ], "budget grant mirror");
      if (!BYTES32_RE.test(String(payload.grant.grantId || ""))
        || String(payload.grant.grantId).toLowerCase() !== String(message.grantId).toLowerCase()
        || normalizeAddress(payload.grant.owner) !== normalizeAddress(message.owner)
        || String(payload.grant.agentScope).toLowerCase() !== String(message.agentScope).toLowerCase()
        || normalizeAddress(payload.grant.token) !== normalizeAddress(message.token)
        || normalizeAddress(payload.grant.payee) !== normalizeAddress(message.payee)
        || canonicalUint(payload.grant.maxTotal, "grant total") !== canonicalUint(message.maxTotal, "budget total")
        || canonicalUint(payload.grant.maxPerPayment, "grant per-payment") !== canonicalUint(message.maxPerPayment, "budget per-payment")
        || canonicalUint(payload.grant.validAfter, "grant start") !== validAfter
        || canonicalUint(payload.grant.validUntil, "grant expiry") !== validUntil
        || normalizeAddress(payload.grant.executionSigner) !== normalizeAddress(message.executionSigner)) {
        throw new Error("budget grant mirror does not match typed data");
      }
      return typed;
    };

    const bindBudget = () => run(async (operation) => {
      const before = await assertConfiguredWallet(operation);
      if (!state.authenticated || state.owner !== before.account) throw new Error("Wallet login is required first");
      await ensureSession(operation);
      const payload = await request("/api/budget/payload", {method: "POST", body: {}}, operation);
      const typed = validateTypedData(payload, before.network, before.account, state.status);
      const signature = await signTyped(typed, operation);
      const result = await request("/api/budget/bind", {method: "POST", body: {signature}}, operation);
      state.phase = result && result.status ? result.status : "budget bound";
      await refreshStatusInternal(operation);
      showMessage("Budget binding verified.");
      return result;
    });

    const extractTransaction = (payload) => {
      if (!isObject(payload)) throw new Error("wallet transaction is invalid");
      return isObject(payload.transaction) ? payload.transaction : payload;
    };
    const validateTransaction = (payload, network, account, kind) => {
      const transaction = extractTransaction(payload);
      assertExactKeys(transaction, ["from", "to", "value", "data", "chainId", "gas", "gasPrice"], "wallet transaction");
      if (normalizeAddress(transaction.from) !== account || normalizeAddress(transaction.to) !== network.token && kind !== "revoke") {
        throw new Error("wallet transaction account or target is invalid");
      }
      if (kind === "revoke" && normalizeAddress(transaction.to) !== network.executor) throw new Error("revoke target is invalid");
      if (String(transaction.value).toLowerCase() !== "0x0") throw new Error("wallet transaction must carry zero native value");
      if (parseInteger(transaction.chainId, "wallet transaction chain id") !== network.chain_id) throw new Error("wallet transaction chain is invalid");
      if (parseQuantity(transaction.gas, "wallet transaction gas") > parseQuantity(network.wallet_transaction.max_gas, "maximum gas")) {
        throw new Error("wallet transaction gas exceeds the configured ceiling");
      }
      if (parseQuantity(transaction.gasPrice, "wallet transaction gas price") > parseQuantity(network.wallet_transaction.max_gas_price_wei, "maximum gas price")) {
        throw new Error("wallet transaction gas price exceeds the configured ceiling");
      }
      const data = String(transaction.data).toLowerCase();
      if (kind === "claim") {
        if (data !== CLAIM_SELECTOR) throw new Error("claim transaction selector is invalid");
        if (!own(payload, "amount_atomic")
          || parseAtomic(payload.amount_atomic, "claim amount") !== parseAtomic(network.terms.claim_atomic, "configured claim amount")) {
          throw new Error("claim amount does not match configured terms");
        }
      }
      if (kind === "allowance") {
        if (!own(payload, "amount_atomic")
          || parseAtomic(payload.amount_atomic, "allowance amount") !== parseAtomic(network.terms.total_atomic, "configured total amount")) {
          throw new Error("allowance amount does not match configured terms");
        }
        const expected = APPROVE_SELECTOR + network.executor.slice(2).padStart(64, "0")
          + parseAtomic(network.terms.total_atomic, "total amount").toString(16).padStart(64, "0");
        if (data !== expected) throw new Error("allowance transaction does not match the configured grant");
      }
      if (kind === "revoke" && (!data.startsWith(REVOKE_SELECTOR)
        || !BYTES32_RE.test("0x" + data.slice(REVOKE_SELECTOR.length)))) {
        throw new Error("revoke transaction selector is invalid");
      }
      return transaction;
    };

    const validateHash = (value) => {
      if (typeof value !== "string" || !HASH_RE.test(value)) throw new Error("wallet transaction hash is invalid");
      return value.toLowerCase();
    };
    const verifyTransaction = async (endpoint, hash, stateKey, operation) => {
      const resultHash = validateHash(hash);
      try {
        const result = await request(endpoint, {method: "POST", body: {transaction_hash: resultHash}}, operation);
        state[stateKey] = result && result.status ? result.status : "verified";
        render();
        return result;
      } catch (error) {
        if (error.status === 409) {
          state[stateKey] = "pending";
          render();
          return error.data || {status: "pending", transaction_hash: resultHash};
        }
        throw error;
      }
    };

    const verifyExistingTransaction = (inputId, hashKey, verifyPath, verificationKey, operation, fallbackHash = null) => {
      if (state.recoveryConflicts[hashKey]) {
        throw new Error("Hosted transaction recovery conflict requires operator review");
      }
      const node = element(inputId);
      const supplied = node && typeof node.value === "string" && node.value.length > 0 ? node.value : fallbackHash;
      const resultHash = validateHash(supplied);
      if (state[hashKey] && state[hashKey] !== resultHash) {
        throw new Error("Existing transaction hash does not match the hosted record");
      }
      state[hashKey] = resultHash;
      return verifyTransaction(verifyPath, resultHash, verificationKey, operation);
    };
    const verifyExistingWithWallet = (inputId, hashKey, verifyPath, verificationKey, fallbackHash = null) => run(async (operation) => {
      const before = await assertConfiguredWallet(operation);
      await ensureSession(operation);
      const current = await assertConfiguredWallet(operation);
      if (!state.authenticated || state.owner !== current.account || before.account !== current.account) {
        throw new Error("Wallet login is required first");
      }
      const result = await verifyExistingTransaction(inputId, hashKey, verifyPath, verificationKey, operation, fallbackHash);
      await refreshStatusInternal(operation);
      return result;
    });
    const sendFundingTransaction = (kind, transactionPath, verifyPath, hashKey, verificationKey) => run(async (operation) => {
      const before = await assertConfiguredWallet(operation);
      await ensureSession(operation);
      if (!state.authenticated || state.owner !== before.account) throw new Error("Wallet login is required first");
      if (state.recoveryConflicts[hashKey]) {
        throw new Error("Hosted transaction recovery conflict requires operator review");
      }
      if (state[hashKey]) {
        const result = await verifyTransaction(verifyPath, state[hashKey], verificationKey, operation);
        await refreshStatusInternal(operation);
        return result;
      }
      const payload = await request(transactionPath, {}, operation);
      const current = await assertConfiguredWallet(operation);
      const transaction = validateTransaction(payload, current.network, current.account, kind);
      const hash = validateHash(await current.provider.request({method: "eth_sendTransaction", params: [transaction]}));
      const recoveryKind = hashKey === "claimHash" ? "claim" : "approval";
      rememberRecoveredHash(recoveryKind, hash, before.account, before.network.chain_id);
      assertCurrent(operation);
      state[hashKey] = hash;
      render();
      const result = await verifyTransaction(verifyPath, hash, verificationKey, operation);
      await refreshStatusInternal(operation);
      return result;
    });

    const claimAllowance = () => sendFundingTransaction(
      "claim", "/api/faucet/transaction", "/api/faucet/verify", "claimHash", "claimVerification",
    );
    const verifyAllowance = () => verifyExistingWithWallet("claim-hash", "claimHash", "/api/faucet/verify", "claimVerification", state.claimHash);
    const allowance = () => sendFundingTransaction(
      "allowance", "/api/allowance/transaction", "/api/allowance/verify", "allowanceHash", "allowanceVerification",
    );
    const verifyAllowanceBinding = () => verifyExistingWithWallet("allowance-hash", "allowanceHash", "/api/allowance/verify", "allowanceVerification", state.allowanceHash);

    const revoke = () => run(async (operation) => {
      const before = await assertConfiguredWallet(operation);
      await ensureSession(operation);
      if (!state.authenticated || state.owner !== before.account) throw new Error("Wallet login is required first");
      await refreshStatusInternal(operation);
      if (state.recoveryConflicts.revokeHash) {
        throw new Error("Hosted transaction recovery conflict requires operator review");
      }
      if (state.revokeHash) {
        const result = await verifyTransaction("/api/revoke/verify", state.revokeHash, "revokeVerification", operation);
        await refreshStatusInternal(operation);
        return result;
      }
      const payload = await request("/api/revoke/prepare", {method: "POST", body: {}}, operation);
      const expectedGrant = state.status && state.status.onboarding && state.status.onboarding.chain_grant_id;
      if (!BYTES32_RE.test(String(expectedGrant || ""))) throw new Error("active grant identifier is unavailable");
      const preparedGrant = payload && (payload.chain_grant_id || payload.grant_id || payload.grantId);
      if (preparedGrant !== undefined && (!BYTES32_RE.test(String(preparedGrant))
        || String(preparedGrant).toLowerCase() !== String(expectedGrant).toLowerCase())) {
        throw new Error("revoke transaction is bound to a different grant");
      }
      const existingRecord = payload && (isObject(payload.record) ? payload.record : payload);
      const existingHash = existingRecord && typeof existingRecord.transaction_hash === "string"
        ? existingRecord.transaction_hash : null;
      if (existingHash) {
        const hash = validateHash(existingHash);
        const statusRecord = state.status && state.status.wallet_operations
          && state.status.wallet_operations.revocation;
        if (statusRecord && typeof statusRecord.transaction_hash === "string"
          && validateHash(statusRecord.transaction_hash) !== hash) {
          markRecoveryConflict("revokeHash", hash, validateHash(statusRecord.transaction_hash));
          throw new Error("Existing revoke hash does not match the hosted record");
        }
        state.revokeHash = hash;
        rememberRecoveredHash("revocation", hash, before.account, before.network.chain_id);
        render();
        const result = await verifyTransaction("/api/revoke/verify", hash, "revokeVerification", operation);
        await refreshStatusInternal(operation);
        return result;
      }
      const transaction = extractTransaction(payload);
      const current = await assertConfiguredWallet(operation);
      const normalized = validateTransaction({transaction}, current.network, current.account, "revoke");
      if (normalized.data.toLowerCase() !== REVOKE_SELECTOR + expectedGrant.slice(2).toLowerCase()) {
        throw new Error("revoke transaction is bound to a different grant");
      }
      const hash = validateHash(await current.provider.request({method: "eth_sendTransaction", params: [normalized]}));
      rememberRecoveredHash("revocation", hash, before.account, before.network.chain_id);
      assertCurrent(operation);
      state.revokeHash = hash;
      render();
      const result = await verifyTransaction("/api/revoke/verify", hash, "revokeVerification", operation);
      await refreshStatusInternal(operation);
      return result;
    });
    const verifyRevoke = () => verifyExistingWithWallet("revoke-hash", "revokeHash", "/api/revoke/verify", "revokeVerification", state.revokeHash);

    const refreshDeviceInternal = async (operation) => {
      if (!requestToken) throw new Error("Pairing request is unavailable");
      await ensureSession(operation);
      const payload = await request("/api/opc/external?request=" + encodeURIComponent(requestToken), {}, operation);
      state.device = payload;
      state.deviceChallenge = payload && payload.challenge ? payload.challenge : null;
      render();
      return payload;
    };
    const refreshDevice = () => run(refreshDeviceInternal);
    const stopDevicePolling = () => {
      if (deviceTimer !== null) {
        clearTimeout(deviceTimer);
        deviceTimer = null;
      }
    };
    const startDevicePolling = () => {
      stopDevicePolling();
      const pollGeneration = generation;
      const poll = async () => {
        if (disposed || generation !== pollGeneration) return;
        try {
          const operation = {generation: pollGeneration, id: ++operationNumber};
          const payload = await refreshDeviceInternal(operation);
          if (payload && ["active", "revoked"].includes(payload.status)) return;
          const expiry = payload && (payload.expires_at || (payload.challenge && payload.challenge.expires_at));
          if (expiry !== undefined && (!Number.isFinite(Date.parse(expiry)) || Date.parse(expiry) <= Date.now())) return;
        } catch (_error) {
          // Polling is best effort; the explicit controls expose errors to the user.
        }
        if (!disposed && generation === pollGeneration) deviceTimer = setTimeout(poll, pollIntervalMs);
      };
      deviceTimer = setTimeout(poll, pollIntervalMs);
    };
    const claimDevice = () => run(async (operation) => {
      if (!requestToken) throw new Error("Pairing request is unavailable");
      const before = await assertConfiguredWallet(operation);
      await ensureSession(operation);
      if (!state.authenticated || state.owner !== before.account) throw new Error("Wallet login is required first");
      const payload = await request("/api/opc/external/claim", {
        method: "POST", body: {request: requestToken},
      }, operation);
      state.device = payload;
      state.deviceChallenge = payload && payload.challenge ? payload.challenge : null;
      render();
      startDevicePolling();
      return payload;
    });
    const approveDevice = () => run(async (operation) => {
      if (!requestToken || !state.deviceChallenge) throw new Error("Device consent challenge is unavailable");
      const before = await assertConfiguredWallet(operation);
      await ensureSession(operation);
      if (!state.authenticated || state.owner !== before.account) throw new Error("Wallet login is required first");
      safeText(state.deviceChallenge.session_id, "device challenge id", 256);
      safeSigningMessage(state.deviceChallenge.message_to_sign, "device challenge message", 4096);
      const expiresAt = Date.parse(state.deviceChallenge.expires_at);
      if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) throw new Error("device consent challenge expired");
      const signature = await signPersonal(state.deviceChallenge.message_to_sign, operation);
      const payload = await request("/api/opc/external/approve", {
        method: "POST", body: {request: requestToken, challenge_id: state.deviceChallenge.session_id, signature},
      }, operation);
      state.device = payload;
      state.deviceChallenge = null;
      stopDevicePolling();
      render();
      return payload;
    });

    const logout = () => run(async (operation) => {
      await ensureSession(operation);
      const result = await request("/api/logout", {method: "POST", body: {}}, operation);
      csrfToken = null;
      sessionKnown = false;
      sessionPromise = null;
      clearOwnerState();
      showMessage("Wallet session ended.");
      render();
      return result;
    });

    const render = () => {
      setText("wallet-state", state.walletName ? state.walletName : "No wallet selected");
      setText("auth-state", state.authenticated ? "Authenticated" : "Wallet login required");
      setText("phase-state", state.phase || "Not started");
      setText("owner", state.owner || state.account || "Not selected");
      setText("chain", state.chainId == null ? "Not read" : state.chainId);
      const network = selectedNetworkValue();
      setText("token", network ? network.token_symbol + " (" + network.token_decimals + " decimals)" : "Not selected");
      setText("budget-state", state.phase || "No budget binding");
      setText("device-state", state.device && state.device.status ? state.device.status : "No device request loaded");
      const deviceList = state.devices.map((device) => device.label + ": " + device.status
        + (device.installation_id ? " (" + device.installation_id + ")" : "")).join("\n");
      setText("device-list", deviceList || "No bound devices");
      const onboarding = state.status && isObject(state.status.onboarding) ? state.status.onboarding : {};
      const terms = state.status && isObject(state.status.terms) ? state.status.terms : {};
      const termsSummary = [
        terms.total !== undefined ? "total " + String(terms.total) : null,
        terms.per_payment !== undefined ? "per payment " + String(terms.per_payment) : null,
        onboarding.grant_starts_at !== undefined ? "starts " + String(onboarding.grant_starts_at) : null,
        onboarding.grant_expires_at !== undefined ? "expires " + String(onboarding.grant_expires_at) : null,
      ].filter(Boolean).join("; ");
      setText("terms-state", termsSummary || "No active authorization terms");
      setText("revoke-state", state.revokeVerification || (state.revokeHash ? "Transaction submitted" : "No revoke requested"));
      for (const [id, value] of [["claim-hash", state.claimHash], ["allowance-hash", state.allowanceHash], ["revoke-hash", state.revokeHash]]) {
        const input = element(id);
        if (input && value) input.value = value;
      }
      const select = element("network-select");
      if (select && config) {
        if (typeof select.replaceChildren === "function" && document && typeof document.createElement === "function") {
          const placeholder = document.createElement("option");
          placeholder.value = ""; placeholder.textContent = "Choose a configured network";
          select.replaceChildren(placeholder);
          for (const item of config.networks) {
            const option = document.createElement("option");
            option.value = String(item.chain_id);
            option.textContent = item.network + " (chain " + item.chain_id + ")";
            select.appendChild(option);
          }
        }
        select.value = selectedNetwork == null ? "" : String(selectedNetwork);
      }
      const walletList = element("wallet-list");
      if (walletList && document && typeof document.createElement === "function" && typeof walletList.replaceChildren === "function") {
        walletList.replaceChildren(...state.wallets.map((wallet) => {
          const button = document.createElement("button");
          button.type = "button"; button.textContent = wallet.name;
          button.disabled = wallet.uuid === state.walletUuid;
          button.addEventListener("click", () => selectProvider(wallet.uuid).catch(walletError));
          return button;
        }));
      }
      const accountList = element("account-list");
      if (accountList && document && typeof document.createElement === "function" && typeof accountList.replaceChildren === "function") {
        accountList.replaceChildren(...state.accounts.map((account) => {
          const button = document.createElement("button");
          button.type = "button"; button.textContent = account;
          button.disabled = account === state.account;
          button.addEventListener("click", () => selectAccount(account));
          return button;
        }));
      }
      const walletEmpty = element("wallet-empty");
      if (walletEmpty) {
        walletEmpty.hidden = state.wallets.length > 0;
        walletEmpty.textContent = state.wallets.length > 0
          ? "" : "No wallet plugin announced an EIP-6963 provider. Open this page in a browser with a wallet plugin.";
      }
      setDisabled("login", state.busy || !state.account || !network);
      setDisabled("grant", state.busy || !state.authenticated);
      setDisabled("budget", state.busy || !state.authenticated);
      setDisabled("claim", state.busy || !state.authenticated);
      setDisabled("allowance", state.busy || !state.authenticated);
      setDisabled("revoke", state.busy || !state.authenticated);
      setDisabled("claim-verify", state.busy || !state.authenticated);
      setDisabled("allowance-verify", state.busy || !state.authenticated);
      setDisabled("revoke-verify", state.busy || !state.authenticated);
      setDisabled("device-refresh", state.busy || !requestToken);
      setDisabled("device-claim", state.busy || !state.authenticated || !requestToken);
      setDisabled("device-approve", state.busy || !state.authenticated || !state.deviceChallenge);
      setDisabled("logout", state.busy);
      setText("advanced-details", network ? JSON.stringify({
        chain_id: network.chain_id, token: network.token, executor: network.executor, payee: network.payee,
        terms: network.terms, wallet_transaction: network.wallet_transaction,
      }, null, 2) : "Choose a configured network to inspect its public deployment details.");
      setText("message", state.message);
    };

    const bindDom = () => {
      if (domBound) return;
      domBound = true;
      const networkSelect = element("network-select");
      if (networkSelect && typeof networkSelect.addEventListener === "function") {
        networkSelect.addEventListener("change", () => {
          try { selectNetwork(networkSelect.value); } catch (error) { walletError(error); }
        });
      }
      const actions = [
        ["login", login], ["grant", grant], ["budget", bindBudget], ["claim", claimAllowance],
        ["claim-verify", verifyAllowance], ["allowance", allowance], ["allowance-verify", verifyAllowanceBinding],
        ["revoke", revoke], ["revoke-verify", verifyRevoke], ["device-refresh", refreshDevice],
        ["device-claim", claimDevice], ["device-approve", approveDevice], ["logout", logout],
      ];
      for (const [id, action] of actions) {
        const node = element(id);
        if (node && typeof node.addEventListener === "function") {
          node.addEventListener("click", () => Promise.resolve(action()).catch(walletError));
        }
      }
    };

    const init = async (initOptions = {}) => {
      if (disposed) throw new Error("binding is disposed");
      bindDom();
      const response = await fetchImpl("/wallet-config.json", {method: "GET", credentials: "same-origin"});
      if (!response.ok) throw new Error("Wallet configuration unavailable");
      config = validateConfig(await response.json());
      discover();
      render();
      if (initOptions.session !== false) {
        await run(async (operation) => {
          await refreshStatusInternal(operation);
          if (requestToken && state.authenticated) {
            try { await refreshDeviceInternal(operation); } catch (_error) { /* device link can be expired */ }
          }
        });
      }
      return publicState();
    };

    walletController = root.AgentonomyWalletSelection.create({
      eventTarget,
      onChange: handleWalletSnapshot,
      onError: walletError,
    });
    bindDom();

    return Object.freeze({
      init, discover, wallets: () => walletController.wallets(),
      selectProvider, selectAccount, selectNetwork, state: publicState,
      snapshot: publicState, login, grant, bindBudget, refreshStatus,
      claimAllowance, verifyAllowance, allowance, verifyAllowanceBinding, revoke,
      claim: claimAllowance, verifyClaim: verifyAllowance,
      approveAllowance: allowance, verifyAllowanceTransaction: verifyAllowanceBinding,
      verifyRevoke,
      refreshDevice, claimDevice, approveDevice, logout,
      stopDevicePolling, dispose: () => {
        disposed = true;
        stopDevicePolling();
        walletController.dispose();
      },
    });
  };

  root.AgentonomyBindingUI = Object.freeze({create});
  const showBootstrapError = (error) => {
    const message = error instanceof Error ? error.message : String(error);
    root.AgentonomyBindingError = message;
    try {
      const node = root.document && typeof root.document.getElementById === "function"
        ? root.document.getElementById("message") : null;
      if (node) node.textContent = message;
    } catch (_error) {
      // Rendering the bootstrap error is best effort.
    }
    return null;
  };
  const start = () => {
    if (root.AgentonomyBindingInstance) return root.AgentonomyBindingReady;
    try {
      const instance = create();
      root.AgentonomyBindingInstance = instance;
      root.AgentonomyBindingReady = Promise.resolve(instance.init(root.__AgentonomyBindingInitOptions || {}))
        .catch(showBootstrapError);
    } catch (error) {
      root.AgentonomyBindingReady = Promise.resolve(showBootstrapError(error));
    }
    return root.AgentonomyBindingReady;
  };
  if (root.document && typeof root.document.addEventListener === "function") {
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", start, {once: true});
    } else {
      start();
    }
  }
})(globalThis);
