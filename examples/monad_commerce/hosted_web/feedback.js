(function installAgentonomyFeedback(root) {
  "use strict";

  const INTENT_RE = /^[A-Za-z0-9_-]{43}$/;
  const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;
  const HASH_RE = /^0x[0-9a-fA-F]{64}$/;
  const HEX_RE = /^0x[0-9a-fA-F]+$/;
  const QUANTITY_RE = /^0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)$/;
  const DECIMAL_RE = /^(?:0|[1-9][0-9]*)$/;
  const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);
  const MAX_DATA_BYTES = 16 * 1024;
  const FEEDBACK_SELECTOR = "0x3c036a7e";
  const STORAGE_PREFIX = "agentonomy.feedback.tx.v1.";
  const STORAGE_STATUS = new Set(["requested", "unknown", "pending", "verified"]);
  const SERVER_STATUS = new Set(["unprepared", "prepared", "requested", "unknown", "pending", "verified"]);

  const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);

  function normalizeAddress(value) {
    return typeof value === "string" && ADDRESS_RE.test(value) ? value.toLowerCase() : null;
  }

  function parseChainId(value, label = "chain") {
    let parsed;
    try {
      if (typeof value === "number" && Number.isSafeInteger(value)) parsed = BigInt(value);
      else if (typeof value === "string" && (HEX_RE.test(value) || DECIMAL_RE.test(value))) parsed = BigInt(value);
      else throw new Error();
    } catch (_) {
      throw new Error(label + " is invalid");
    }
    if (parsed <= 0n || parsed > MAX_SAFE) throw new Error(label + " is invalid");
    return Number(parsed);
  }

  function parseQuantity(value, label, {allowZero = false, max = null} = {}) {
    if (typeof value !== "string" || !(QUANTITY_RE.test(value) || DECIMAL_RE.test(value))) {
      throw new Error(label + " is invalid");
    }
    let parsed;
    try { parsed = BigInt(value); } catch (_) { throw new Error(label + " is invalid"); }
    if ((!allowZero && parsed <= 0n) || (allowZero && parsed < 0n)) throw new Error(label + " is invalid");
    if (max !== null && parsed > max) throw new Error(label + " exceeds the configured ceiling");
    return parsed;
  }

  function normalizeHash(value, label = "transaction hash") {
    if (typeof value !== "string" || !HASH_RE.test(value) || /^0x0{64}$/i.test(value)) {
      throw new Error(label + " is invalid");
    }
    return value.toLowerCase();
  }

  function normalizeBytes32(value, label) {
    return normalizeHash(value, label);
  }

  function feedbackIntent(location, explicit) {
    const value = explicit || String((location && location.pathname) || "").split("/").pop();
    if (!INTENT_RE.test(value || "")) throw new Error("feedback link is invalid");
    return value;
  }

  function currentOrigin(location) {
    const origin = location && typeof location.origin === "string" ? location.origin.replace(/\/+$/, "") : "";
    return /^https?:\/\/[^/]+$/i.test(origin) ? origin : "";
  }

  function normalizeRegistry(value, chainId) {
    if (typeof value !== "string") return null;
    const caip = new RegExp("^eip155:" + String(chainId) + ":(0x[0-9a-fA-F]{40})$").exec(value);
    if (caip) return caip[1].toLowerCase();
    return normalizeAddress(value);
  }

  function safePublicUri(value, location) {
    if (typeof value !== "string" || value.length < 1 || value.length > 2048) {
      throw new Error("feedback document is invalid");
    }
    let parsed;
    try { parsed = new URL(value, currentOrigin(location) || undefined); } catch (_) {
      throw new Error("feedback document is invalid");
    }
    if (!/^https?:$/.test(parsed.protocol) || currentOrigin(location) && parsed.origin !== currentOrigin(location)) {
      throw new Error("feedback document is invalid");
    }
    return parsed.href;
  }

  function parseScore(value) {
    if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= 100) return value;
    if (typeof value === "string" && DECIMAL_RE.test(value)) {
      const parsed = Number(value);
      if (Number.isSafeInteger(parsed) && parsed >= 0 && parsed <= 100) return parsed;
    }
    throw new Error("feedback score is invalid");
  }

  function parseUint256(value, label) {
    let parsed;
    try {
      if (typeof value === "bigint") parsed = value;
      else if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) parsed = BigInt(value);
      else if (typeof value === "string" && DECIMAL_RE.test(value)) parsed = BigInt(value);
      else throw new Error();
    } catch (_) {
      throw new Error(label + " is invalid");
    }
    if (parsed < 0n || parsed >= (1n << 256n)) throw new Error(label + " is invalid");
    return parsed;
  }

  function utf8Hex(value, label) {
    if (typeof value !== "string") throw new Error(label + " is invalid");
    const bytes = new TextEncoder().encode(value);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  function abiWord(value, label) {
    return parseUint256(value, label).toString(16).padStart(64, "0");
  }

  function abiString(value, label) {
    const encoded = utf8Hex(value, label);
    const paddedLength = Math.ceil(encoded.length / 64) * 64;
    return abiWord(encoded.length / 2, label + " length") + encoded.padEnd(paddedLength, "0");
  }

  function feedbackCalldata(agentId, score, feedbackHash, feedbackUri, location) {
    const normalizedHash = normalizeBytes32(feedbackHash, "feedback hash");
    const normalizedScore = parseScore(score);
    const origin = currentOrigin(location);
    if (!origin) throw new Error("feedback origin is invalid");
    const expectedUri = origin + "/erc8004/feedback/" + normalizedHash.slice(2) + ".json";
    if (feedbackUri !== expectedUri) throw new Error("feedback URI is invalid");
    const strings = ["starred", "csv-reconciliation", origin + "/", feedbackUri];
    const encodedStrings = strings.map((value, index) => abiString(value, "feedback string " + index));
    const offsets = [];
    let offset = 32 * 8;
    for (const encoded of encodedStrings) {
      offsets.push(abiWord(offset, "feedback offset"));
      offset += encoded.length / 2;
    }
    return FEEDBACK_SELECTOR
      + abiWord(agentId, "agent id")
      + abiWord(normalizedScore, "feedback score")
      + abiWord(0, "feedback decimals")
      + offsets.join("")
      + normalizedHash.slice(2)
      + encodedStrings.join("");
  }

  function normalizeOutputHash(value) {
    if (typeof value !== "string") throw new Error("result proof is invalid");
    if (/^0x[0-9a-fA-F]{64}$/.test(value)) return "sha256:" + value.slice(2).toLowerCase();
    if (/^sha256:[0-9a-fA-F]{64}$/.test(value)) return "sha256:" + value.slice(7).toLowerCase();
    throw new Error("result proof is invalid");
  }

  function normalizeVerificationStatus(value) {
    if (value == null) return null;
    if (value === "current" || value === "unconfirmed") return value;
    throw new Error("feedback verification status is invalid");
  }

  function normalizeTimestamp(value) {
    if (value == null) return null;
    if (typeof value !== "string" || value.length > 64
        || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(value)) {
      throw new Error("feedback verification time is invalid");
    }
    const milliseconds = Date.parse(value);
    if (!Number.isFinite(milliseconds)) throw new Error("feedback verification time is invalid");
    return new Date(milliseconds).toISOString();
  }

  function parseCookie(name, document) {
    const cookieText = document && typeof document.cookie === "string" ? document.cookie : "";
    for (const item of cookieText.split(";")) {
      const index = item.indexOf("=");
      if (index < 0 || item.slice(0, index).trim() !== name) continue;
      const value = item.slice(index + 1).trim();
      if (/^[A-Za-z0-9_-]{20,512}$/.test(value)) return value;
    }
    return null;
  }

  function genericRequestError(status) {
    const error = new Error(
      status === 401 ? "Wallet authorization is required. Open the wallet page first."
        : status === 403 ? "Wallet authorization expired or this action was blocked."
          : status === 404 ? "This feedback link is unavailable or expired."
            : status === 409 ? "The feedback status could not be confirmed."
              : "The feedback request is temporarily unavailable.",
    );
    error.status = status;
    return error;
  }

  function create(options = {}) {
    if (!isObject(options)) throw new TypeError("feedback options are required");
    const document = options.document || root.document || null;
    const eventTarget = options.eventTarget || root;
    const fetchImpl = options.fetch || root.fetch;
    const location = options.location || root.location || {pathname: "", origin: ""};
    if (!document || typeof document.getElementById !== "function") throw new TypeError("feedback document is unavailable");
    if (!isObject(eventTarget) || typeof eventTarget.addEventListener !== "function") {
      throw new TypeError("feedback event target is unavailable");
    }
    if (typeof fetchImpl !== "function") throw new TypeError("feedback fetch is unavailable");
    if (!root.AgentonomyWalletSelection || typeof root.AgentonomyWalletSelection.create !== "function") {
      throw new Error("wallet selection is unavailable");
    }

    const intent = feedbackIntent(location, options.intent);
    const state = {
      intent,
      loaded: false,
      loading: false,
      status: "unprepared",
      owner: null,
      chainId: null,
      score: null,
      feedbackHash: null,
      feedbackUri: null,
      disclosure: null,
      registry: null,
      agentId: null,
      agentRegistry: null,
      verificationStatus: null,
      lastVerifiedAt: null,
      walletTransaction: null,
      transaction: null,
      transactionHash: null,
      verified: false,
      isRevoked: false,
      feedbackIndex: null,
      localRecord: null,
      awaitingRecovery: false,
      authRequired: false,
      wallets: [],
      accounts: [],
      walletUuid: null,
      walletName: null,
      selectedAccount: null,
      walletChainId: null,
      snapshot: null,
      busy: false,
      message: "Loading the feedback request…",
    };

    let disposed = false;
    let walletController = null;
    let domBound = false;

    const element = (id) => document.getElementById(id);
    const setText = (id, value) => {
      const node = element(id);
      if (node) node.textContent = value == null ? "" : String(value);
    };
    const setHidden = (id, hidden) => {
      const node = element(id);
      if (node) node.hidden = Boolean(hidden);
    };
    const showMessage = (value) => {
      state.message = String(value);
      setText("message", state.message);
    };

    function storage() {
      try {
        const value = options.storage !== undefined ? options.storage : root.localStorage;
        return value && typeof value.getItem === "function" && typeof value.setItem === "function"
          ? value : null;
      } catch (_) {
        return null;
      }
    }

    function storageKey(owner, feedbackHash) {
      return STORAGE_PREFIX + owner + "." + feedbackHash;
    }

    function validLocalRecord(value, owner, feedbackHash) {
      if (!isObject(value) || value.version !== 1
          || value.owner !== owner || value.feedback_hash !== feedbackHash
          || !STORAGE_STATUS.has(value.status)) return null;
      let transactionHash = null;
      if (value.transaction_hash !== null && value.transaction_hash !== undefined) {
        try { transactionHash = normalizeHash(value.transaction_hash); } catch (_) { return null; }
      }
      return {
        version: 1,
        owner,
        feedback_hash: feedbackHash,
        status: value.status,
        transaction_hash: transactionHash,
        intent: typeof value.intent === "string" && INTENT_RE.test(value.intent) ? value.intent : null,
      };
    }

    function readLocalRecord(owner = state.owner, feedbackHash = state.feedbackHash) {
      if (!owner || !feedbackHash) return null;
      const store = storage();
      if (!store) return null;
      try {
        const raw = store.getItem(storageKey(owner, feedbackHash));
        if (!raw) return null;
        return validLocalRecord(JSON.parse(raw), owner, feedbackHash);
      } catch (_) {
        return null;
      }
    }

    function persistLocalRecord(record) {
      const owner = normalizeAddress(record && record.owner);
      const feedbackHash = record && typeof record.feedback_hash === "string"
        ? normalizeBytes32(record.feedback_hash, "feedback hash") : null;
      if (!owner || !feedbackHash || !STORAGE_STATUS.has(record.status)) return false;
      const store = storage();
      if (!store) return false;
      const value = {
        version: 1,
        owner,
        feedback_hash: feedbackHash,
        status: record.status,
        transaction_hash: record.transaction_hash == null ? null : normalizeHash(record.transaction_hash),
        intent,
      };
      try {
        store.setItem(storageKey(owner, feedbackHash), JSON.stringify(value));
        state.localRecord = value;
        return true;
      } catch (_) {
        return false;
      }
    }

    function removeLocalRecord() {
      if (!state.owner || !state.feedbackHash) return false;
      const store = storage();
      if (!store || typeof store.removeItem !== "function") return false;
      try { store.removeItem(storageKey(state.owner, state.feedbackHash)); } catch (_) { return false; }
      state.localRecord = null;
      return true;
    }

    function sendLockName(owner, feedbackHash) {
      return "agentonomy.feedback.send." + owner.toLowerCase() + "." + feedbackHash.toLowerCase();
    }

    function webLocks() {
      const navigator = root.navigator || root.window && root.window.navigator;
      const locks = navigator && navigator.locks;
      if (!locks || typeof locks.request !== "function") {
        throw new Error("This browser cannot safely coordinate the wallet request across tabs");
      }
      return locks;
    }

    function validateWalletTransaction(transaction, payload) {
      if (!isObject(transaction)) throw new Error("feedback transaction is invalid");
      const allowed = new Set(["from", "to", "value", "data", "chainId", "gas"]);
      if (Object.keys(transaction).some((key) => !allowed.has(key))
          || ["from", "to", "value", "data", "chainId", "gas"].some((key) => !own(transaction, key))) {
        throw new Error("feedback transaction is invalid");
      }
      const from = normalizeAddress(transaction.from);
      const to = normalizeAddress(transaction.to);
      const expectedOwner = normalizeAddress(payload && payload.owner) || state.owner;
      const expectedChainId = parseChainId(payload && payload.chainId, "feedback network");
      const expectedRegistry = normalizeRegistry(payload && payload.registry, expectedChainId);
      if (!from || from !== expectedOwner || !to || to !== expectedRegistry) {
        throw new Error("feedback transaction target is invalid");
      }
      if ((state.owner && expectedOwner !== state.owner) || (state.chainId && expectedChainId !== state.chainId)
          || parseChainId(transaction.chainId, "feedback transaction chain") !== expectedChainId) {
        throw new Error("feedback transaction network is invalid");
      }
      if (typeof transaction.value !== "string" || parseQuantity(transaction.value, "native value", {allowZero: true}) !== 0n) {
        throw new Error("feedback transaction value is invalid");
      }
      if (typeof transaction.data !== "string" || !HEX_RE.test(transaction.data)
          || transaction.data.length < 10 || (transaction.data.length - 2) % 2 !== 0
          || transaction.data.length > 2 + MAX_DATA_BYTES * 2
          || transaction.data.slice(0, 10).toLowerCase() !== FEEDBACK_SELECTOR) {
        throw new Error("feedback calldata is invalid");
      }
      let expectedData;
      try {
        expectedData = feedbackCalldata(
          payload && payload.agentId,
          payload && payload.score,
          payload && payload.feedbackHash,
          payload && payload.feedbackUri,
          location,
        );
      } catch (_) {
        throw new Error("feedback calldata is invalid");
      }
      if (transaction.data.toLowerCase() !== expectedData) throw new Error("feedback calldata is invalid");
      const walletLimits = payload && payload.wallet_transaction;
      if (!isObject(walletLimits)) throw new Error("feedback wallet limits are invalid");
      const maxGas = parseQuantity(walletLimits.max_gas, "maximum gas");
      const maxGasPrice = parseQuantity(walletLimits.max_gas_price_wei, "maximum gas price");
      const gas = parseQuantity(transaction.gas, "feedback gas", {max: maxGas});
      return Object.freeze({
        from,
        to,
        value: "0x0",
        data: transaction.data.toLowerCase(),
        chainId: "0x" + expectedChainId.toString(16),
        gas: "0x" + gas.toString(16),
      });
    }

    function validateDisclosure(value, expectedOwner, expectedScore) {
      if (!isObject(value)) return null;
      const buyer = normalizeAddress(value.buyer || value.owner);
      if (buyer && buyer !== expectedOwner) throw new Error("feedback disclosure owner is invalid");
      const score = value.score === undefined ? null : parseScore(value.score);
      if (score !== null && score !== expectedScore) throw new Error("feedback disclosure score is invalid");
      const payment = value.payment_transaction_hash == null
        ? null : normalizeHash(value.payment_transaction_hash, "payment proof");
      const output = value.output_hash == null ? null : normalizeOutputHash(value.output_hash);
      return {buyer, score, payment, output};
    }

    function validatePayload(value) {
      if (!isObject(value) || !SERVER_STATUS.has(value.status)) throw new Error("feedback status is invalid");
      const owner = normalizeAddress(value.owner);
      const chainId = parseChainId(value.chain_id, "feedback network");
      if (!owner) throw new Error("feedback owner is invalid");
      if (state.owner && state.owner !== owner) throw new Error("feedback owner changed");
      if (state.chainId && state.chainId !== chainId) throw new Error("feedback network changed");
      const score = value.score === undefined || value.score === null ? null : parseScore(value.score);
      const feedbackHash = value.feedback_hash == null ? null : normalizeBytes32(value.feedback_hash, "feedback hash");
      if (value.status !== "unprepared" && (!feedbackHash || score === null)) {
        throw new Error("feedback draft is incomplete");
      }
      const registry = normalizeRegistry(
        value.reputation_registry
          || value.reputationRegistry
          || value.identity && (value.identity.reputation_registry || value.identity.reputationRegistry),
        chainId,
      );
      const identity = isObject(value.identity) ? value.identity : null;
      const agentIdValue = own(value, "agent_id") ? value.agent_id
        : own(value, "agentId") ? value.agentId
          : identity && (own(identity, "agent_id") ? identity.agent_id : identity.agentId);
      const agentRegistryValue = own(value, "agent_registry") ? value.agent_registry
        : own(value, "agentRegistry") ? value.agentRegistry
          : identity && (identity.agent_registry || identity.agentRegistry);
      const agentId = agentIdValue == null ? null : parseUint256(agentIdValue, "agent id").toString(10);
      const agentRegistry = agentRegistryValue == null ? null : normalizeRegistry(agentRegistryValue, chainId);
      if (value.transaction && !registry) throw new Error("feedback registry is invalid");
      const feedbackUri = value.feedback_uri == null ? null : safePublicUri(value.feedback_uri, location);
      if (value.transaction && (agentId === null || !agentRegistry || !feedbackUri)) {
        throw new Error("feedback identity is invalid");
      }
      const transaction = value.transaction ? validateWalletTransaction(value.transaction, {
        ...value, status: value.status, owner, chainId, registry,
        agentId, agentRegistry, feedbackHash, feedbackUri,
      }) : null;
      const transactionHash = value.transaction_hash == null ? null
        : normalizeHash(value.transaction_hash);
      const verified = value.status === "verified" || value.verified === true;
      const verificationStatus = normalizeVerificationStatus(value.verification_status);
      const lastVerifiedAt = normalizeTimestamp(value.last_verified_at);
      if (verified && (value.status !== "verified" || value.verified !== true || !transactionHash
          || !Number.isSafeInteger(value.feedback_index) || value.feedback_index < 1
          || typeof value.is_revoked !== "boolean")) {
        throw new Error("feedback proof is invalid");
      }
      if (!verified && value.verified === true) throw new Error("feedback proof is invalid");
      if (verificationStatus === "current" && !verified) throw new Error("feedback verification status is invalid");
      const disclosure = validateDisclosure(value.disclosure, owner, score);
      const walletTransaction = isObject(value.wallet_transaction) ? {
        max_gas: parseQuantity(value.wallet_transaction.max_gas, "maximum gas").toString(10),
        max_gas_price_wei: parseQuantity(value.wallet_transaction.max_gas_price_wei, "maximum gas price").toString(10),
      } : null;
      if (transaction && !walletTransaction) throw new Error("feedback wallet limits are invalid");
      return {
        status: value.status,
        owner,
        chainId,
        score,
        feedbackHash,
        feedbackUri,
        registry,
        agentId,
        agentRegistry,
        transaction,
        walletTransaction,
        transactionHash,
        disclosure,
        verificationStatus,
        lastVerifiedAt,
        verified,
        isRevoked: value.is_revoked === true,
        feedbackIndex: verified ? value.feedback_index : null,
      };
    }

    function applyPayload(value) {
      const next = validatePayload(value);
      state.owner = next.owner;
      state.chainId = next.chainId;
      state.score = next.score;
      state.feedbackHash = next.feedbackHash;
      state.feedbackUri = next.feedbackUri;
      state.registry = next.registry || state.registry;
      state.agentId = next.agentId || state.agentId;
      state.agentRegistry = next.agentRegistry || state.agentRegistry;
      state.verificationStatus = next.verificationStatus;
      state.lastVerifiedAt = next.lastVerifiedAt;
      state.transaction = next.transaction || state.transaction;
      state.walletTransaction = next.walletTransaction || state.walletTransaction;
      state.transactionHash = next.transactionHash || state.transactionHash;
      state.disclosure = next.disclosure || state.disclosure;
      state.status = next.status;
      state.verified = next.verified;
      state.isRevoked = next.isRevoked;
      state.feedbackIndex = next.feedbackIndex;
      state.localRecord = readLocalRecord(state.owner, state.feedbackHash);
      if (state.localRecord && state.localRecord.transaction_hash
          && state.transactionHash && state.localRecord.transaction_hash !== state.transactionHash) {
        throw new Error("feedback transaction changed");
      }
      if (state.localRecord && state.localRecord.transaction_hash) {
        state.transactionHash = state.localRecord.transaction_hash;
      }
      if (state.verified) {
        state.awaitingRecovery = false;
        if (!persistLocalRecord({
          owner: state.owner, feedback_hash: state.feedbackHash, status: "verified",
          transaction_hash: state.transactionHash,
        })) {
          // A verified server response is still safe to display; inability to
          // update local storage cannot create a resend path.
          state.localRecord = null;
        }
      } else {
        state.awaitingRecovery = Boolean(
          state.transactionHash
            || state.localRecord && STORAGE_STATUS.has(state.localRecord.status),
            );
      }
      if (state.status === "prepared" && state.localRecord
          && STORAGE_STATUS.has(state.localRecord.status)) state.awaitingRecovery = true;
      if (state.transactionHash) {
        const input = element("transaction-hash");
        if (input && !input.value) input.value = state.transactionHash;
      }
      return next;
    }

    function walletProvider() {
      return walletController ? walletController.provider() : null;
    }

    function authorizedWallet() {
      if (!state.snapshot || !state.selectedAccount || !state.owner) {
        throw new Error("Choose the authorized wallet account first");
      }
      walletController.assertSnapshot(state.snapshot);
      if (state.selectedAccount !== state.owner) throw new Error("Selected wallet does not own this feedback");
      const provider = walletProvider();
      if (!provider || typeof provider.request !== "function") throw new Error("Selected wallet is unavailable");
      return provider;
    }

    async function assertWalletNetwork() {
      const provider = authorizedWallet();
      const chainValue = await provider.request({method: "eth_chainId"});
      const chainId = parseChainId(chainValue, "wallet network");
      if (chainId !== state.chainId) throw new Error("Wallet network does not match the feedback request");
      const accounts = await provider.request({method: "eth_accounts"});
      if (!Array.isArray(accounts) || !accounts.map(normalizeAddress).includes(state.owner)) {
        throw new Error("Wallet account is no longer authorized");
      }
      state.walletChainId = chainId;
      return provider;
    }

    async function assertGasPrice(provider) {
      if (!state.walletTransaction) throw new Error("Feedback wallet limits are unavailable");
      const current = await provider.request({method: "eth_gasPrice"});
      const currentPrice = parseQuantity(current, "current gas price", {allowZero: true});
      const maximum = parseQuantity(state.walletTransaction.max_gas_price_wei, "maximum gas price");
      if (currentPrice > maximum) throw new Error("Current network fee exceeds the configured ceiling");
      return currentPrice;
    }

    function legacyProvider() {
      const candidates = [
        root.okxwallet,
        root.ethereum,
        root.window && root.window.okxwallet,
        root.window && root.window.ethereum,
      ];
      return candidates.find((candidate) => candidate && typeof candidate.request === "function") || null;
    }

    function announceLegacy() {
      if (walletController.wallets().length > 0) return;
      const provider = legacyProvider();
      if (!provider) return;
      eventTarget.dispatchEvent({
        type: "eip6963:announceProvider",
        detail: {info: {uuid: "legacy-browser-wallet", name: "Browser wallet", rdns: "legacy.browser.wallet"}, provider},
      });
    }

    function walletSnapshotChanged(snapshot) {
      if (disposed) return;
      state.snapshot = snapshot;
      state.wallets = walletController.wallets();
      state.accounts = snapshot.accounts.slice();
      state.walletUuid = snapshot.walletUuid;
      state.walletName = snapshot.walletName;
      state.selectedAccount = snapshot.account;
      state.walletChainId = snapshot.chainId;
      render();
    }

    function walletError(error) {
      if (error && error.status === 401) state.authRequired = true;
      showMessage(error && error.message ? error.message : "Wallet selection failed.");
      render();
    }

    function renderWalletChoices() {
      const walletList = element("wallet-list");
      if (walletList && typeof walletList.replaceChildren === "function") {
        const children = state.wallets.map((wallet) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = wallet.name;
          button.setAttribute("aria-pressed", wallet.uuid === state.walletUuid ? "true" : "false");
          button.disabled = state.busy && wallet.uuid === state.walletUuid;
          button.addEventListener("click", () => walletController.selectProvider(wallet.uuid).catch(walletError));
          return button;
        });
        walletList.replaceChildren(...children);
      }
      const accountList = element("account-list");
      if (accountList && typeof accountList.replaceChildren === "function") {
        const children = state.accounts.map((account) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = account;
          button.setAttribute("aria-pressed", account === state.selectedAccount ? "true" : "false");
          button.disabled = state.busy && account === state.selectedAccount;
          button.addEventListener("click", () => {
            try { walletController.selectAccount(account); } catch (error) { walletError(error); }
          });
          return button;
        });
        accountList.replaceChildren(...children);
      }
      setHidden("wallet-empty", state.wallets.length > 0);
    }

    function render() {
      renderWalletChoices();
      setText("owner", state.selectedAccount || "Not selected");
      setText("wallet-chain", state.walletChainId == null ? "Not read" : state.walletChainId);
      setText("request-chain", state.chainId == null ? "Not loaded" : state.chainId);
      setText("score", state.score == null ? "Not loaded" : state.score + " / 100");
      setText("feedback-hash", state.feedbackHash || "Not loaded");
      setText("payment-hash", state.disclosure && state.disclosure.payment || "Not disclosed");
      setText("output-hash", state.disclosure && state.disclosure.output || "Not disclosed");
      const verificationLabel = state.isRevoked
        ? "Revoked"
        : !state.verified
          ? state.verificationStatus === "unconfirmed" ? "Current recheck unconfirmed" : state.status
          : state.verificationStatus === "current" ? "Current chain verification"
            : state.verificationStatus === "unconfirmed" ? "Verified record · current recheck unconfirmed"
              : "Verified record";
      const canRecheck = Boolean(state.verified && state.transactionHash
        && !state.isRevoked && state.verificationStatus !== "current");
      setText("feedback-state", verificationLabel);
      setText("service-identity", state.agentId == null || !state.agentRegistry
        ? "Not loaded" : "Agent #" + state.agentId + " · " + state.agentRegistry);
      setText("registry", state.registry || "Not loaded");
      setText("native-value", state.transaction ? state.transaction.value : "Not loaded");
      setText("gas", state.transaction ? state.transaction.gas : "Not loaded");
      setText("calldata", state.transaction ? "Validated ERC-8004 feedback call" : "Not loaded");
      setText("network-fee", state.walletTransaction
        ? "Checked before send · max gas price " + state.walletTransaction.max_gas_price_wei + " wei"
        : "Not loaded");
      setText("verified-hash", state.transactionHash || "Not available");
      setText("feedback-index", state.feedbackIndex == null ? "Not available" : state.feedbackIndex);
      setText("last-verified-at", state.lastVerifiedAt || "Not available");
      setText("verification-freshness", verificationLabel);
      setText("verified-kicker", state.isRevoked ? "REVOKED" : "VERIFIED");
      setText("verified-title", state.isRevoked ? "Feedback was recorded and revoked"
        : state.verificationStatus === "current" ? "Feedback is verified on the current chain"
          : "Feedback is a verified record");
      setText("verified-copy", state.isRevoked
        ? "This feedback was recorded and is revoked. No wallet action is available."
        : state.verificationStatus === "current"
          ? "The original transaction was independently verified against the current chain state."
          : state.verificationStatus === "unconfirmed"
            ? "The prior verified record remains on file, but the latest chain recheck was not confirmed. Recheck the original transaction below; this page will never resend it."
            : "This feedback is a verified record. Current chain confirmation was not performed on this page.");
      setHidden("auth-help", !state.authRequired);
      setHidden("revoked-note", !state.isRevoked);
      setHidden("recovery-card", (!state.awaitingRecovery && !canRecheck) || state.isRevoked);
      setHidden("verified-card", !state.verified);
      const send = element("send");
      const ready = Boolean(state.transaction && state.status === "prepared" && !state.awaitingRecovery
        && !state.verified && !state.isRevoked && state.selectedAccount === state.owner
        && state.walletChainId === state.chainId);
      if (send) send.disabled = state.busy || !ready;
      const verify = element("verify");
      const input = element("transaction-hash");
      if (verify) {
        verify.textContent = canRecheck ? "Recheck original transaction" : "Verify original transaction";
        verify.disabled = state.busy || state.isRevoked
          || (state.verified && state.verificationStatus === "current")
          || !state.awaitingRecovery && !canRecheck
        || !input || !HASH_RE.test(input.value || "");
      }
      setText("recovery-title", canRecheck ? "Recheck the original transaction" : "Verify the original transaction");
      setText("recovery-copy", canRecheck
        ? "This record was previously verified. Recheck the original transaction below; this page will never send a replacement."
        : "The wallet response was interrupted or a transaction is already pending. Find the original hash in your wallet and verify that transaction here. This page will never send a replacement.");
      const mismatch = state.selectedAccount && state.owner && state.selectedAccount !== state.owner;
      if (mismatch) setText("send-help", "Choose the authorized account shown by the wallet session.");
      else if (state.awaitingRecovery) setText("send-help", "An original transaction is already recorded. Verify it below; do not send again.");
      else if (state.isRevoked) setText("send-help", "This feedback is revoked and read-only.");
      else if (state.verified) setText("send-help", "This feedback is verified and read-only.");
      else if (!state.transaction) setText("send-help", "The Agent has not prepared a sendable feedback transaction.");
      else setText("send-help", "Review the disclosure, then explicitly send this one transaction.");
      setText("message", state.message);
    }

    async function request(path, method = "GET", body = undefined) {
      const headers = {};
      const optionsForFetch = {method, credentials: "same-origin", cache: "no-store", headers};
      if (method !== "GET") {
        const csrf = options.csrfToken || parseCookie("agentonomy_hosted_csrf", document);
        if (!csrf) throw genericRequestError(403);
        headers["Content-Type"] = "application/json";
        headers["X-Agentonomy-CSRF"] = csrf;
        optionsForFetch.body = JSON.stringify(body || {});
      }
      const response = await fetchImpl(path, optionsForFetch);
      let payload = null;
      try { payload = await response.json(); } catch (_) { payload = null; }
      if (!response.ok) throw genericRequestError(response.status);
      if (!isObject(payload)) throw genericRequestError(409);
      return payload;
    }

    async function refresh() {
      if (state.busy) return null;
      state.loading = true;
      try {
        const value = await request("/api/feedback/" + encodeURIComponent(intent), "GET");
        applyPayload(value);
        state.loaded = true;
        state.authRequired = false;
        if (state.isRevoked) showMessage("This feedback is revoked. No wallet action is available.");
        else if (state.verified && state.verificationStatus === "current") {
          showMessage("This feedback was independently verified against the current chain. No wallet action is available.");
        } else if (state.verified) {
          showMessage("This is a verified record from an earlier chain observation. Recheck the original transaction if needed; it will not be resent.");
        }
        else if (state.awaitingRecovery) showMessage("An original transaction is pending. Verify that transaction below; it will not be resent.");
        else showMessage("Review the disclosure before sending the prepared feedback transaction.");
        return value;
      } catch (error) {
        state.authRequired = error && error.status === 401;
        showMessage(error && error.message ? error.message : "The feedback request is unavailable.");
        return null;
      } finally {
        state.loading = false;
        render();
      }
    }

    async function send() {
      if (state.busy || state.verified || state.awaitingRecovery || state.isRevoked) return null;
      if (!state.transaction || state.status !== "prepared") {
        showMessage("Feedback is not ready to send");
        render();
        return null;
      }
      const owner = state.owner;
      const feedbackHash = state.feedbackHash;
      if (!owner || !feedbackHash) {
        showMessage("The feedback draft is unavailable.");
        render();
        return null;
      }
      state.busy = true;
      render();
      try {
        const locks = webLocks();
        return await locks.request(sendLockName(owner, feedbackHash), {
          mode: "exclusive", ifAvailable: true,
        }, async (lock) => {
          if (!lock) {
            showMessage("Another tab is handling this feedback. Verify its original transaction; this page will not resend.");
            return null;
          }
          const durable = readLocalRecord(owner, feedbackHash);
          if (durable && STORAGE_STATUS.has(durable.status)) {
            state.localRecord = durable;
            state.transactionHash = durable.transaction_hash || state.transactionHash;
            state.awaitingRecovery = true;
            state.status = durable.status;
            showMessage("An original wallet request is already recorded. Verify that transaction; this page will not resend.");
            return null;
          }
          // Re-read wallet account, network, and fee while the exclusive lock is held,
          // immediately before persisting the send marker and opening the wallet.
          const provider = await assertWalletNetwork();
          await assertGasPrice(provider);
          if (!persistLocalRecord({owner, feedback_hash: feedbackHash, status: "requested", transaction_hash: null})) {
            throw new Error("Local recovery storage is unavailable; the transaction was not sent");
          }
          state.awaitingRecovery = true;
          state.status = "requested";
          render();
          let sent;
          try {
            sent = await provider.request({method: "eth_sendTransaction", params: [state.transaction]});
          } catch (error) {
            if (error && error.code === 4001) {
              if (removeLocalRecord()) {
                state.awaitingRecovery = false;
                state.status = "prepared";
                showMessage("The wallet canceled the request. Review it again before sending.");
              } else {
                state.awaitingRecovery = true;
                state.status = "unknown";
                showMessage("The wallet response could not be cleared safely. Find the original hash; this page will not resend.");
              }
              return null;
            }
            persistLocalRecord({owner, feedback_hash: feedbackHash, status: "unknown", transaction_hash: null});
            showMessage("The wallet response was inconclusive. Find the original hash in the wallet; this page will not resend.");
            return null;
          }
          let transactionHash;
          try { transactionHash = normalizeHash(sent); } catch (_) {
            persistLocalRecord({owner, feedback_hash: feedbackHash, status: "unknown", transaction_hash: null});
            showMessage("The wallet response was inconclusive. Find the original hash in the wallet; this page will not resend.");
            return null;
          }
          if (!persistLocalRecord({owner, feedback_hash: feedbackHash, status: "pending", transaction_hash: transactionHash})) {
            state.transactionHash = transactionHash;
            showMessage("The transaction was sent. Save its original hash and verify it below; it will not be resent.");
            return null;
          }
          state.transactionHash = transactionHash;
          state.status = "pending";
          state.awaitingRecovery = true;
          showMessage("Transaction sent. Verify the original transaction below.");
          return transactionHash;
        });
      } catch (error) {
        showMessage(error && error.message ? error.message : "The wallet request was not sent.");
        return null;
      } finally {
        state.busy = false;
        render();
      }
    }

    async function verify(hashValue = null) {
      const canRecheck = Boolean(state.verified && state.transactionHash
        && !state.isRevoked && state.verificationStatus !== "current");
      if (state.busy || state.isRevoked || state.verified && !canRecheck) return null;
      const input = element("transaction-hash");
      let transactionHash;
      try { transactionHash = normalizeHash(hashValue || input && input.value); } catch (_) {
        showMessage("Enter the original transaction hash to verify.");
        render();
        return null;
      }
      if (state.transactionHash && state.transactionHash !== transactionHash) {
        showMessage("Use the original transaction hash; a replacement is not accepted.");
        render();
        return null;
      }
      if (!state.owner || !state.feedbackHash) {
        showMessage("The feedback draft is unavailable.");
        render();
        return null;
      }
      if (!persistLocalRecord({
        owner: state.owner, feedback_hash: state.feedbackHash, status: "pending",
        transaction_hash: transactionHash,
      })) {
        showMessage("Local recovery storage is unavailable; verification was not sent.");
        render();
        return null;
      }
      state.transactionHash = transactionHash;
      state.awaitingRecovery = true;
      state.status = "pending";
      state.busy = true;
      if (input) input.value = transactionHash;
      render();
      try {
        const value = await request("/api/feedback/" + encodeURIComponent(intent) + "/verify", "POST", {
          transaction_hash: transactionHash,
        });
        applyPayload(value);
        if (state.isRevoked) {
          showMessage("This feedback is revoked. No wallet action is available.");
        } else if (state.verified && state.verificationStatus === "current") {
          showMessage("The original feedback transaction was independently verified against the current chain.");
        } else if (state.verified && state.verificationStatus === "unconfirmed") {
          showMessage("The prior verified record remains on file, but the latest chain recheck was not confirmed. This page will not resend.");
        } else if (state.verified) {
          showMessage("The original transaction remains a verified record; its current chain state was not confirmed. This page will not resend.");
        } else {
          showMessage("The original transaction is recorded; wait for verification and retry this same hash.");
        }
        return value;
      } catch (error) {
        showMessage(error && error.message ? error.message : "The original transaction could not be verified.");
        return null;
      } finally {
        state.busy = false;
        render();
      }
    }

    function bindDom() {
      if (domBound) return;
      domBound = true;
      const sendButton = element("send");
      if (sendButton) sendButton.addEventListener("click", () => send());
      const verifyButton = element("verify");
      if (verifyButton) verifyButton.addEventListener("click", () => verify());
      const input = element("transaction-hash");
      if (input) input.addEventListener("input", render);
    }

    async function init() {
      bindDom();
      walletController = root.AgentonomyWalletSelection.create({
        eventTarget,
        onChange: walletSnapshotChanged,
        onError: walletError,
      });
      walletController.discover();
      announceLegacy();
      render();
      await refresh();
      return publicState();
    }

    function publicState() {
      return JSON.parse(JSON.stringify({
        intent: state.intent,
        status: state.status,
        owner: state.owner,
        chain_id: state.chainId,
        score: state.score,
        feedback_hash: state.feedbackHash,
        transaction_hash: state.transactionHash,
        verified: state.verified,
        is_revoked: state.isRevoked,
        verification_status: state.verificationStatus,
        last_verified_at: state.lastVerifiedAt,
        awaiting_recovery: state.awaitingRecovery,
        wallet: {
          name: state.walletName,
          account: state.selectedAccount,
          chain_id: state.walletChainId,
        },
        message: state.message,
      }));
    }

    function dispose() {
      disposed = true;
      if (walletController) walletController.dispose();
    }

    return Object.freeze({
      init,
      refresh,
      discover: () => walletController && walletController.discover(),
      wallets: () => walletController ? walletController.wallets() : [],
      selectProvider: (uuid) => walletController.selectProvider(uuid),
      selectAccount: (account) => walletController.selectAccount(account),
      send,
      verify,
      state: publicState,
      dispose,
    });
  }

  root.AgentonomyFeedbackUI = Object.freeze({create});
  const start = () => {
    if (root.AgentonomyFeedbackInstance) return root.AgentonomyFeedbackReady;
    try {
      const instance = create();
      root.AgentonomyFeedbackInstance = instance;
      root.AgentonomyFeedbackReady = instance.init().catch((error) => {
        const message = error && error.message ? error.message : "Feedback page unavailable.";
        try {
          const node = root.document && root.document.getElementById("message");
          if (node) node.textContent = message;
        } catch (_) { /* best effort */ }
        return null;
      });
      return root.AgentonomyFeedbackReady;
    } catch (error) {
      root.AgentonomyFeedbackReady = Promise.resolve(null);
      return root.AgentonomyFeedbackReady;
    }
  };
  if (root.document && typeof root.document.addEventListener === "function") {
    if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", start);
    else start();
  }
})(globalThis);
