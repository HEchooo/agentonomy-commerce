(() => {
  "use strict";

  const CHAIN_ID = 10143;
  const CHAIN_HEX = "0x279f";
  const TOKEN_DECIMALS = 6;
  const SUPPLY_ATOMIC = 1000000;
  const PER_PAYMENT_ATOMIC = 500000;
  const GRANT_DURATION_SECONDS = 86460;
  // PublicCanary._wallet_transaction fixes owner approval/revoke gas at
  // 100,000; accept lower values but reject drift above that server ceiling.
  const MAX_TX_GAS = 100000n;
  const MAX_GAS_PRICE_WEI = 500000000000n;
  const EIP712_DOMAIN_NAME = "Agentonomy Budget Executor";
  const EIP712_DOMAIN_VERSION = "1";
  const APPROVE_SELECTOR = "095ea7b3";
  const REVOKE_SELECTOR = "b75c7dc6";
  const EXPLORER_TX = "https://testnet.monadexplorer.com/tx/";
  const NETWORK = {
    chainId: CHAIN_HEX,
    chainName: "Monad Testnet",
    nativeCurrency: {name: "MON", symbol: "MON", decimals: 18},
    rpcUrls: ["https://testnet-rpc.monad.xyz"],
    blockExplorerUrls: ["https://testnet.monadexplorer.com"],
  };

  const state = {
    status: null,
    phase: "wallet",
    provider: null,
    providerBound: false,
    connected: false,
    account: null,
    chainId: null,
    sessionPromise: null,
    initPromise: null,
    allowanceTxHash: null,
    allowanceVerificationPending: false,
    allowanceVerified: false,
    allowanceRejectedTxHash: null,
    allowanceTransaction: null,
    previewId: null,
    idempotencyKey: null,
    purchaseId: null,
    purchaseState: null,
    purchaseSettlement: null,
    purchaseServiceMode: null,
    coreRevoked: false,
    revokePrepared: false,
    revokeTransaction: null,
    revokeTxHash: null,
    revokeRejectedTxHash: null,
    chainRevoked: false,
    busy: false,
    message: "正在连接本地会话……",
  };

  const $ = (id) => document.getElementById(id);

  function text(id, value) {
    const node = $(id);
    if (node) node.textContent = value == null ? "—" : String(value);
  }

  function normalizeAddress(value) {
    return typeof value === "string" && /^0x[0-9a-fA-F]{40}$/.test(value)
      ? value.toLowerCase()
      : null;
  }

  function parseChainId(value) {
    if (typeof value === "number" && Number.isInteger(value) && value >= 0) return value;
    if (typeof value === "string" && /^0x[0-9a-fA-F]+$/.test(value)) return Number.parseInt(value, 16);
    if (typeof value === "string" && /^[0-9]+$/.test(value)) return Number.parseInt(value, 10);
    return null;
  }

  function quantity(value, field) {
    if (typeof value !== "string" || !/^0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)$/.test(value)) {
      throw new Error(`${field} invalid`);
    }
    const parsed = BigInt(value);
    if (parsed <= 0n) throw new Error(`${field} invalid`);
    return value.toLowerCase();
  }

  function hash(value) {
    return typeof value === "string" && /^0x[0-9a-fA-F]{64}$/.test(value) ? value.toLowerCase() : null;
  }

  function verifiedPurchaseSettlement(value) {
    if (!value || typeof value !== "object") return null;
    const transactionHash = hash(value.transaction_hash);
    const expectedToken = normalizeAddress(state.status && state.status.token);
    if (
      !transactionHash ||
      value.verified !== true ||
      value.status !== "verified" ||
      parseChainId(value.chain_id) !== CHAIN_ID ||
      value.receipt_status !== 1 ||
      value.two_rpc_verified !== true ||
      String(value.amount_atomic) !== "300000" ||
      !expectedToken ||
      normalizeAddress(value.token) !== expectedToken
    ) return null;
    return {transaction_hash: transactionHash};
  }

  function purchaseSettlementSummary() {
    if (!state.purchaseSettlement) return null;
    const note = state.purchaseServiceMode === "local_anvil"
      ? "；报告含历史环境标签，实际付款以已复验的 Monad Testnet 回执为准"
      : "";
    return `付款已复验 · Monad Testnet ${CHAIN_ID} · 0.30 TestUSD · 付款交易 ${state.purchaseSettlement.transaction_hash}${note}`;
  }

  function provider() {
    if (state.provider) return state.provider;
    if (typeof window !== "undefined" && window.okxwallet) return window.okxwallet;
    if (typeof globalThis !== "undefined" && globalThis.okxwallet) return globalThis.okxwallet;
    return null;
  }

  function friendlyError(error) {
    if (error && error.code === 4001) return "用户已取消，当前步骤未改变。";
    if (error && error.code === 4902) return "钱包尚未添加 Monad Testnet，请点击“切换／添加网络”。";
    if (error && error.status === 404) return "当前记录不存在，请保留原订单引用后重试。";
    if (error && error.status === 409) return "链上状态仍在确认，请稍后重试相同验证。";
    return "操作未完成，请检查钱包连接和当前阶段后重试。";
  }

  function setMessage(value) {
    state.message = value;
    text("message", value);
  }

  async function request(path, method = "GET", body) {
    const options = {method, credentials: "same-origin"};
    if (body !== undefined) {
      options.headers = {"Content-Type": "application/json"};
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    let payload = null;
    try {
      payload = await response.json();
    } catch (_) {
      payload = null;
    }
    if (!response.ok) {
      const error = new Error("request failed");
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  async function ensureSession() {
    if (!state.sessionPromise) state.sessionPromise = request("/api/session", "POST", {});
    return state.sessionPromise;
  }

  function statusData() {
    if (!state.status) throw new Error("status unavailable");
    return state.status;
  }

  async function refreshStatus() {
    const current = await request("/api/status");
    if (!current || typeof current !== "object") throw new Error("status unavailable");
    state.status = current;
    state.phase = current.onboarding && current.onboarding.phase
      ? current.onboarding.phase
      : "wallet";
    hydrateWalletOperations(current);
    render();
    return current;
  }

  function hydrateWalletOperations(current) {
    const operations = current && current.wallet_operations;
    if (!operations || typeof operations !== "object") return;

    const approval = operations.approval;
    if (approval && typeof approval === "object") {
      const rejected = approval.status === "rejected";
      const txHash = hash(approval.transaction_hash);
      const rejectedHash = hash(approval.rejected_transaction_hash);
      const allowanceInput = $("allowance-hash");
      const activeHash = state.allowanceTxHash || hash(allowanceInput && allowanceInput.value);
      state.allowanceRejectedTxHash = rejectedHash;
      if (rejected) {
        // A conclusively rejected candidate is no longer an active operation.
        // Keep unknown/pending candidates immutable, but let the user enter a
        // corrected hash after the server has reset a rejected record.
        if (!activeHash || rejectedHash === activeHash) {
          state.allowanceTxHash = null;
          state.allowanceVerificationPending = false;
          state.allowanceVerified = false;
          state.allowanceTransaction = null;
          if (allowanceInput) allowanceInput.value = "";
        } else {
          // A stale rejected journal entry cannot override a different local
          // hash whose verification request may not have reached the server.
          state.allowanceTxHash = activeHash;
          state.allowanceVerificationPending = true;
          state.allowanceVerified = false;
        }
      } else if (txHash) {
        state.allowanceTxHash = txHash;
        state.allowanceVerificationPending = approval.verified !== true;
        state.allowanceVerified = approval.verified === true || approval.status === "verified";
      }
    }

    const revocation = operations.revocation;
    if (revocation && typeof revocation === "object") {
      const rejected = revocation.status === "rejected";
      const txHash = hash(revocation.transaction_hash);
      const rejectedHash = hash(revocation.rejected_transaction_hash);
      const revokeInput = $("revoke-hash");
      const activeHash = state.revokeTxHash || hash(revokeInput && revokeInput.value);
      state.revokeRejectedTxHash = rejectedHash;
      if (revocation.core_revoked === true) state.coreRevoked = true;
      if (rejected) {
        if (!activeHash || rejectedHash === activeHash) {
          state.revokeTxHash = null;
          state.revokeTransaction = null;
          state.revokePrepared = false;
          state.chainRevoked = false;
          if (revokeInput) revokeInput.value = "";
        } else {
          state.revokeTxHash = activeHash;
          state.chainRevoked = false;
        }
      } else if (txHash) {
        state.revokeTxHash = txHash;
        state.revokePrepared = true;
        state.chainRevoked = revocation.verified === true
          || revocation.chain_revoked === true
          || revocation.status === "verified";
      }
    }

    const onboarding = current.onboarding;
    if (onboarding && (onboarding.phase === "revoked" || onboarding.grant_status === "revoked")) {
      state.coreRevoked = true;
    }
  }

  function clearPendingUi(message = "钱包账户或网络已改变，待确认步骤已清除。") {
    state.connected = false;
    state.account = null;
    state.chainId = null;
    // A wallet event invalidates authorization for new signing, but it must
    // never erase a hash returned by a transaction that may already be on-chain.
    state.allowanceTransaction = null;
    state.revokeTransaction = null;
    // A prepared revoke transaction is tied to the wallet state used to
    // prepare it.  If no hash was broadcast yet, require an explicit prepare
    // after reconnecting; a known hash remains a verification-only recovery.
    if (!state.revokeTxHash) state.revokePrepared = false;
    const allowanceHash = $("allowance-hash");
    if (allowanceHash && state.allowanceTxHash) allowanceHash.value = state.allowanceTxHash;
    const revokeHash = $("revoke-hash");
    if (revokeHash && state.revokeTxHash) revokeHash.value = state.revokeTxHash;
    setMessage(message);
    render();
  }

  function bindProvider(candidate) {
    if (!candidate || state.providerBound && state.provider === candidate) return candidate;
    state.provider = candidate;
    if (typeof candidate.on === "function") {
      candidate.on("accountsChanged", () => clearPendingUi());
      candidate.on("chainChanged", () => clearPendingUi());
    }
    state.providerBound = true;
    return candidate;
  }

  async function providerRequest(method, params = []) {
    const candidate = bindProvider(provider());
    if (!candidate || typeof candidate.request !== "function") {
      throw new Error("wallet unavailable");
    }
    return candidate.request({method, params});
  }

  async function ensureWalletReady({requestAccounts = false} = {}) {
    const configuredOwner = normalizeAddress(statusData().owner);
    if (!configuredOwner) throw new Error("configured owner unavailable");
    const accounts = await providerRequest(requestAccounts ? "eth_requestAccounts" : "eth_accounts");
    const connected = Array.isArray(accounts) && accounts.length > 0 ? normalizeAddress(accounts[0]) : null;
    if (!connected || connected !== configuredOwner) {
      state.connected = false;
      state.account = connected;
      throw new Error("wrong wallet");
    }
    const chain = await providerRequest("eth_chainId");
    if (parseChainId(chain) !== CHAIN_ID) {
      state.connected = false;
      state.account = connected;
      state.chainId = chain;
      throw new Error("wrong chain");
    }
    state.connected = true;
    state.account = connected;
    state.chainId = CHAIN_HEX;
    render();
    return {owner: configuredOwner, chainId: CHAIN_ID};
  }

  async function connect() {
    try {
      await refreshStatus();
      const candidate = bindProvider(provider());
      if (!candidate) {
        setMessage("未检测到 OKX 钱包，请使用安装 OKX 扩展的浏览器打开此 localhost 页面。");
        return false;
      }
      await ensureWalletReady({requestAccounts: true});
      setMessage("钱包已连接。请按当前阶段逐步完成授权。");
      render();
      return true;
    } catch (error) {
      state.connected = false;
      setMessage(error && error.code === 4001 ? "用户已取消，当前步骤未改变。" : "钱包账户或网络不符合当前 Monad Testnet 配置。");
      render();
      return false;
    }
  }

  async function switchNetwork() {
    try {
      bindProvider(provider());
      try {
        await providerRequest("wallet_switchEthereumChain", [{chainId: CHAIN_HEX}]);
      } catch (error) {
        if (!error || error.code !== 4902) throw error;
        await providerRequest("wallet_addEthereumChain", [NETWORK]);
      }
      setMessage("网络已切换，请重新连接钱包以继续。");
      state.connected = false;
      render();
      return true;
    } catch (error) {
      setMessage(friendlyError(error));
      render();
      return false;
    }
  }

  function utf8Hex(value) {
    if (typeof value !== "string") throw new Error("message invalid");
    const bytes = new TextEncoder().encode(value);
    return `0x${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
  }

  async function run(action) {
    if (state.busy) return null;
    state.busy = true;
    render();
    try {
      const result = await action();
      render();
      return result;
    } catch (error) {
      setMessage(friendlyError(error));
      render();
      return null;
    } finally {
      state.busy = false;
      render();
    }
  }

  async function verifyWallet() {
    return run(async () => {
      const first = await ensureWalletReady();
      const challenge = await request("/api/wallet/challenge", "POST", {});
      if (!challenge || typeof challenge.message_to_sign !== "string") throw new Error("challenge invalid");
      const current = await ensureWalletReady();
      const signature = await providerRequest("personal_sign", [utf8Hex(challenge.message_to_sign), current.owner]);
      await ensureWalletReady();
      await request("/api/wallet/verify", "POST", {signature});
      await refreshStatus();
      setMessage("钱包身份已验证。");
      return first;
    });
  }

  async function verifyGrant() {
    return run(async () => {
      await ensureWalletReady();
      const challenge = await request("/api/grant/challenge", "POST", {});
      if (!challenge || typeof challenge.message_to_sign !== "string") throw new Error("challenge invalid");
      const current = await ensureWalletReady();
      const signature = await providerRequest("personal_sign", [utf8Hex(challenge.message_to_sign), current.owner]);
      await ensureWalletReady();
      await request("/api/grant/verify", "POST", {signature});
      await refreshStatus();
      setMessage("业务预算授权已验证。");
    });
  }

  function field(object, ...names) {
    if (!object || typeof object !== "object") return undefined;
    for (const name of names) if (Object.prototype.hasOwnProperty.call(object, name)) return object[name];
    return undefined;
  }

  function exactKeys(value, keys) {
    return !!value && typeof value === "object"
      && !Array.isArray(value)
      && Object.keys(value).sort().join("\u0000") === keys.slice().sort().join("\u0000");
  }

  function bytes32(value, name) {
    if (typeof value !== "string" || !/^0x[0-9a-fA-F]{64}$/.test(value)
        || /^0x0{64}$/.test(value.toLowerCase())) throw new Error(`${name} invalid`);
    return value.toLowerCase();
  }

  function uintValue(value, name) {
    if (typeof value === "number") {
      if (!Number.isSafeInteger(value) || value < 0) throw new Error(`${name} invalid`);
      return BigInt(value);
    }
    if (typeof value !== "string" || !/^(?:0|[1-9][0-9]*)$/.test(value)) {
      throw new Error(`${name} invalid`);
    }
    return BigInt(value);
  }

  function sameTypedSchema(actual, expected, name) {
    if (!Array.isArray(actual) || actual.length !== expected.length) throw new Error(`${name} invalid`);
    for (let index = 0; index < expected.length; index += 1) {
      const item = actual[index];
      const target = expected[index];
      if (!item || typeof item !== "object" || !exactKeys(item, ["name", "type"])
          || item.name !== target.name || item.type !== target.type) throw new Error(`${name} invalid`);
    }
  }

  function timestampSeconds(value, name) {
    if (typeof value !== "string") throw new Error(`${name} invalid`);
    const milliseconds = Date.parse(value);
    if (!Number.isFinite(milliseconds)) throw new Error(`${name} invalid`);
    return BigInt(Math.floor(milliseconds / 1000));
  }

  function validateTypedData(payload, expected) {
    const typed = payload && payload.typed_data ? payload.typed_data : payload;
    if (!typed || !exactKeys(typed, ["types", "primaryType", "domain", "message"])) {
      throw new Error("typed data invalid");
    }
    const types = typed.types;
    const domain = typed.domain;
    const message = typed.message;
    if (!exactKeys(types, ["EIP712Domain", "SpendGrant"]) || !exactKeys(domain, ["name", "version", "chainId", "verifyingContract"])) {
      throw new Error("typed schema invalid");
    }
    if (typed.primaryType !== "SpendGrant") throw new Error("typed primary type invalid");
    sameTypedSchema(types.EIP712Domain, [
      {name: "name", type: "string"},
      {name: "version", type: "string"},
      {name: "chainId", type: "uint256"},
      {name: "verifyingContract", type: "address"},
    ], "typed domain schema");
    sameTypedSchema(types.SpendGrant, [
      {name: "grantId", type: "bytes32"},
      {name: "owner", type: "address"},
      {name: "agentScope", type: "bytes32"},
      {name: "token", type: "address"},
      {name: "payee", type: "address"},
      {name: "maxPerPayment", type: "uint256"},
      {name: "maxTotal", type: "uint256"},
      {name: "validAfter", type: "uint256"},
      {name: "validUntil", type: "uint256"},
      {name: "executionSigner", type: "address"},
    ], "typed grant schema");
    if (!exactKeys(message, ["grantId", "owner", "agentScope", "token", "payee", "maxPerPayment", "maxTotal", "validAfter", "validUntil", "executionSigner"])) {
      throw new Error("typed message schema invalid");
    }
    if (payload && payload.digest !== undefined
        && (!hash(payload.digest) || /^0x0{64}$/.test(String(payload.digest).toLowerCase()))) {
      throw new Error("typed digest invalid");
    }
    const expectedOwner = normalizeAddress(expected.owner);
    const expectedExecutor = normalizeAddress(expected.executor);
    const expectedToken = normalizeAddress(expected.token);
    const expectedPayee = normalizeAddress(expected.payee);
    const expectedExecutionSigner = normalizeAddress(
      field(expected, "execution_signer") || field(expected.onboarding, "execution_signer"),
    );
    if (!expectedOwner || !expectedExecutor || !expectedToken || !expectedPayee || !expectedExecutionSigner) {
      throw new Error("typed configuration invalid");
    }
    if (domain.name !== EIP712_DOMAIN_NAME || domain.version !== EIP712_DOMAIN_VERSION
        || parseChainId(domain.chainId) !== CHAIN_ID) throw new Error("typed domain invalid");
    if (normalizeAddress(domain.verifyingContract) !== expectedExecutor) {
      throw new Error("typed executor invalid");
    }
    if (bytes32(message.grantId, "typed grant id") === null) throw new Error("typed grant id invalid");
    if (normalizeAddress(message.owner) !== expectedOwner) throw new Error("typed owner invalid");
    if (bytes32(message.agentScope, "typed agent scope") === null) throw new Error("typed agent scope invalid");
    if (normalizeAddress(message.token) !== expectedToken) throw new Error("typed token invalid");
    if (normalizeAddress(message.payee) !== expectedPayee) throw new Error("typed payee invalid");
    if (normalizeAddress(message.executionSigner) !== expectedExecutionSigner) throw new Error("typed execution signer invalid");
    if (uintValue(message.maxTotal, "typed total") !== BigInt(SUPPLY_ATOMIC)) throw new Error("typed total invalid");
    if (uintValue(message.maxPerPayment, "typed payment") !== BigInt(PER_PAYMENT_ATOMIC)) throw new Error("typed payment invalid");
    const validAfter = uintValue(message.validAfter, "typed validAfter");
    const validUntil = uintValue(message.validUntil, "typed validUntil");
    if (validUntil <= validAfter || validUntil - validAfter !== BigInt(GRANT_DURATION_SECONDS)) {
      throw new Error("typed duration invalid");
    }
    const onboarding = expected.onboarding || {};
    if (field(onboarding, "grant_starts_at") !== undefined
        && validAfter !== timestampSeconds(onboarding.grant_starts_at, "typed grant start")) {
      throw new Error("typed start invalid");
    }
    if (field(onboarding, "grant_expires_at") !== undefined
        && validUntil !== timestampSeconds(onboarding.grant_expires_at, "typed grant expiry")) {
      throw new Error("typed expiry invalid");
    }
    if (payload && payload.typed_data && !payload.grant) throw new Error("typed grant missing");
    if (payload && payload.grant) {
      const grant = payload.grant;
      if (!exactKeys(grant, ["grantId", "owner", "agentScope", "token", "payee", "maxPerPayment", "maxTotal", "validAfter", "validUntil", "executionSigner"])) {
        throw new Error("grant schema invalid");
      }
      if (bytes32(grant.grantId, "grant id") !== bytes32(message.grantId, "typed grant id")
          || normalizeAddress(grant.owner) !== normalizeAddress(message.owner)
          || bytes32(grant.agentScope, "agent scope") !== bytes32(message.agentScope, "typed agent scope")
          || normalizeAddress(grant.token) !== normalizeAddress(message.token)
          || normalizeAddress(grant.payee) !== normalizeAddress(message.payee)
          || uintValue(grant.maxTotal, "grant total") !== uintValue(message.maxTotal, "typed total")
          || uintValue(grant.maxPerPayment, "grant payment") !== uintValue(message.maxPerPayment, "typed payment")
          || uintValue(grant.validAfter, "grant validAfter") !== validAfter
          || uintValue(grant.validUntil, "grant validUntil") !== validUntil
          || normalizeAddress(grant.executionSigner) !== normalizeAddress(message.executionSigner)) {
        throw new Error("grant binding invalid");
      }
    }
    return true;
  }

  async function bindBudget() {
    return run(async () => {
      const expected = statusData();
      const payload = await request("/api/budget/payload");
      validateTypedData(payload, expected);
      const current = await ensureWalletReady();
      const signature = await providerRequest("eth_signTypedData_v4", [current.owner, JSON.stringify(payload.typed_data)]);
      await ensureWalletReady();
      await request("/api/budget/bind", "POST", {signature});
      await refreshStatus();
      setMessage("链上预算授权已绑定。");
    });
  }

  function approvalData(executor) {
    const normalized = normalizeAddress(executor);
    if (!normalized) throw new Error("executor invalid");
    return `0x${APPROVE_SELECTOR}${normalized.slice(2).padStart(64, "0")}${SUPPLY_ATOMIC.toString(16).padStart(64, "0")}`;
  }

  function boundedQuantity(value, name, maximum) {
    const normalized = quantity(value, name);
    if (BigInt(normalized) > maximum) throw new Error(`${name} exceeds configured ceiling`);
    return normalized;
  }

  function validateAllowanceTransaction(payload, expected) {
    const tx = payload && payload.transaction;
    if (!tx || typeof tx !== "object") throw new Error("allowance transaction invalid");
    if (!exactKeys(tx, ["from", "to", "value", "data", "chainId", "gas", "gasPrice"])) {
      throw new Error("allowance transaction schema invalid");
    }
    const expectedOwner = normalizeAddress(expected.owner);
    const expectedToken = normalizeAddress(expected.token);
    if (!expectedOwner || !expectedToken || !normalizeAddress(expected.executor)) throw new Error("allowance configuration invalid");
    if (normalizeAddress(tx.from) !== expectedOwner) throw new Error("allowance owner invalid");
    if (normalizeAddress(tx.to) !== expectedToken) throw new Error("allowance token invalid");
    if (parseChainId(tx.chainId) !== CHAIN_ID) throw new Error("allowance chain invalid");
    if (typeof tx.value !== "string" || !/^0x0$/.test(tx.value.toLowerCase())) throw new Error("allowance value invalid");
    if (typeof tx.data !== "string" || tx.data.toLowerCase() !== approvalData(expected.executor)) throw new Error("allowance data invalid");
    const gas = boundedQuantity(tx.gas, "gas", MAX_TX_GAS);
    const gasPrice = boundedQuantity(tx.gasPrice, "gasPrice", MAX_GAS_PRICE_WEI);
    return {
      from: expectedOwner,
      to: expectedToken,
      value: "0x0",
      data: approvalData(expected.executor),
      chainId: CHAIN_HEX,
      gas,
      gasPrice,
    };
  }

  async function verifyAllowanceAction(hashValue = state.allowanceTxHash) {
      const txHash = hash(hashValue || $("allowance-hash") && $("allowance-hash").value);
      if (!txHash) throw new Error("allowance hash invalid");
      state.allowanceTxHash = txHash;
      state.allowanceVerificationPending = true;
      state.allowanceVerified = false;
      render();
      let result;
      try {
        result = await request("/api/allowance/verify", "POST", {transaction_hash: txHash});
      } catch (error) {
        if (!error || error.status !== 409) throw error;
        try {
          await refreshStatus();
        } catch (_) {
          // A failed status read cannot prove that the candidate is invalid.
          // Keep its hash and let the user retry the same verification.
          state.allowanceTxHash = txHash;
          state.allowanceVerificationPending = true;
          setMessage("allowance 状态暂不可用；已保留原交易哈希，请重试复验。");
          render();
          return null;
        }
        if (state.allowanceTxHash) {
          state.allowanceVerificationPending = true;
          setMessage("allowance 交易仍待链上复验；已保留原交易哈希，请重试同一验证。");
        } else {
          state.allowanceVerificationPending = false;
          setMessage("allowance 证据已拒绝；请输入正确交易哈希后重新复验，不会自动重发。");
        }
        render();
        return null;
      }
      const verified = !!(result && (result.verified === true || result.status === "verified"));
      state.allowanceVerified = verified;
      const rejected = !!(result && result.status === "rejected");
      state.allowanceVerificationPending = !verified && !rejected;
      if (verified) {
        await refreshStatus();
        setMessage("1.00 TestUSD allowance 已独立复验。");
      } else if (rejected) {
        state.allowanceRejectedTxHash = hash(result.rejected_transaction_hash) || txHash;
        state.allowanceTxHash = null;
        state.allowanceTransaction = null;
        const input = $("allowance-hash");
        if (input) input.value = "";
        setMessage("allowance 证据已拒绝；请输入正确交易哈希后重新复验，不会自动重发。");
      } else {
        setMessage("allowance 交易已发送，等待链上复验；点击同一交易哈希重试，不会再次发送。");
      }
      return result;
  }

  async function verifyAllowance(hashValue = state.allowanceTxHash) {
    return run(() => verifyAllowanceAction(hashValue));
  }

  async function approveAllowance() {
    return run(async () => {
      if (state.allowanceTxHash && state.allowanceVerificationPending) return verifyAllowanceAction(state.allowanceTxHash);
      const expected = statusData();
      const payload = await request("/api/allowance/transaction");
      const transaction = validateAllowanceTransaction(payload, expected);
      state.allowanceTransaction = transaction;
      await ensureWalletReady();
      const txHash = hash(await providerRequest("eth_sendTransaction", [transaction]));
      if (!txHash) throw new Error("allowance hash invalid");
      state.allowanceTxHash = txHash;
      state.allowanceVerificationPending = true;
      state.allowanceVerified = false;
      const hashInput = $("allowance-hash");
      if (hashInput) hashInput.value = txHash;
      addTxLink("tx-links", txHash);
      render();
      const result = await verifyAllowanceAction(txHash);
      // Retain/report the hash before this post-send readiness check.  If the
      // wallet changed after broadcast, run() surfaces the interruption while
      // the same hash remains available for explicit verification retry.
      await ensureWalletReady();
      return result;
    });
  }

  function randomId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    throw new Error("browser randomness unavailable");
  }

  async function preview() {
    return run(async () => {
      const csvNode = $("csv");
      const csvText = csvNode ? csvNode.value : "";
      if (!csvText) throw new Error("CSV required");
      state.idempotencyKey = state.idempotencyKey || randomId();
      const result = await request("/api/preview", "POST", {csv_text: csvText, idempotency_key: state.idempotencyKey});
      if (!result || typeof result.preview_id !== "string") throw new Error("preview invalid");
      state.previewId = result.preview_id;
      setMessage("报价已固定。确认购买后，系统只会恢复同一订单，不会重复扣款。");
      return result;
    });
  }

  function updatePurchase(result) {
    state.purchaseId = result && (result.purchase_id || result.id) || state.purchaseId;
    if (result && typeof result.preview_id === "string" && result.preview_id) state.previewId = result.preview_id;
    state.purchaseState = result && (result.state || result.status) || null;
    state.purchaseSettlement = verifiedPurchaseSettlement(result && result.settlement);
    const stateText = state.purchaseState || "尚未创建订单";
    text("purchase-status", stateText);
    const purchaseInput = $("purchase-id");
    if (state.purchaseId && purchaseInput) purchaseInput.value = state.purchaseId;
    const output = result && result.service_result;
    state.purchaseServiceMode = output && typeof output === "object" && typeof output.settlement_mode === "string"
      ? output.settlement_mode
      : null;
    const resultNode = $("result");
    if (resultNode) resultNode.textContent = output ? JSON.stringify(output, null, 2) : "请查询／恢复当前订单。";
    return result;
  }

  async function executePurchase() {
    return run(async () => {
      if (!state.previewId) throw new Error("preview required");
      const result = await request("/api/execute", "POST", {preview_id: state.previewId});
      updatePurchase(result);
      setMessage(result && (result.state === "delivered" || result.status === "delivered")
        ? "报告已交付。重复查询同一订单不会再次扣款。"
        : "订单仍在处理；请查询／恢复同一订单，不要创建新的付款。");
      return result;
    });
  }

  async function queryPurchase() {
    return run(async () => {
      const input = $("purchase-id");
      const purchaseId = input && input.value ? input.value.trim() : state.purchaseId;
      if (!purchaseId) throw new Error("purchase required");
      const result = await request(`/api/purchases/${encodeURIComponent(purchaseId)}`);
      return updatePurchase(result);
    });
  }

  async function recoverPurchase() {
    return run(async () => {
      const input = $("purchase-id");
      const purchaseId = input && input.value ? input.value.trim() : state.purchaseId;
      if (!purchaseId) throw new Error("purchase required");
      const result = await request(`/api/purchases/${encodeURIComponent(purchaseId)}/recover`, "POST", {});
      updatePurchase(result);
      setMessage(result && result.state === "delivered"
        ? "原订单报告已恢复交付；不会再次扣款。"
        : "原订单已保留付款证据，交付仍待完成；可继续恢复同一订单。",
      );
      return result;
    });
  }

  function revokeData(grantId) {
    const normalized = bytes32(grantId, "grant id");
    return `0x${REVOKE_SELECTOR}${normalized.slice(2)}`;
  }

  function validateRevokeTransaction(payload, expected) {
    const tx = payload && payload.transaction;
    if (!tx || typeof tx !== "object") throw new Error("revoke transaction invalid");
    if (!exactKeys(tx, ["from", "to", "value", "data", "chainId", "gas", "gasPrice"])) {
      throw new Error("revoke transaction schema invalid");
    }
    const configuredGrantId = field(expected.onboarding, "chain_grant_id", "chainGrantId");
    const normalizedGrantId = bytes32(configuredGrantId, "configured chain grant id");
    const suppliedGrantId = field(payload, "grant_id", "grantId", "chain_grant_id", "chainGrantId")
      ?? field(tx, "grant_id", "grantId", "chain_grant_id", "chainGrantId");
    if (suppliedGrantId !== undefined
        && bytes32(suppliedGrantId, "revoke grant id") !== normalizedGrantId) {
      throw new Error("revoke grant id invalid");
    }
    const expectedOwner = normalizeAddress(expected.owner);
    const expectedExecutor = normalizeAddress(expected.executor);
    if (!expectedOwner || !expectedExecutor) throw new Error("revoke configuration invalid");
    if (normalizeAddress(tx.from) !== expectedOwner) throw new Error("revoke owner invalid");
    if (normalizeAddress(tx.to) !== expectedExecutor) throw new Error("revoke executor invalid");
    if (parseChainId(tx.chainId) !== CHAIN_ID) throw new Error("revoke chain invalid");
    if (typeof tx.value !== "string" || tx.value.toLowerCase() !== "0x0") throw new Error("revoke value invalid");
    const data = revokeData(normalizedGrantId);
    if (typeof tx.data !== "string" || tx.data.toLowerCase() !== data) throw new Error("revoke data invalid");
    return {from: expectedOwner, to: expectedExecutor, value: "0x0", data, chainId: CHAIN_HEX,
      gas: boundedQuantity(tx.gas, "gas", MAX_TX_GAS),
      gasPrice: boundedQuantity(tx.gasPrice, "gasPrice", MAX_GAS_PRICE_WEI)};
  }

  async function prepareRevoke() {
    return run(async () => {
      const result = await request("/api/revoke/prepare", "POST", {});
      const expected = statusData();
      state.coreRevoked = !!(result && (
        result.core_revoked === true ||
        result.core_status === "revoked" ||
        result.core && result.core.status === "revoked" ||
        expected.onboarding && (expected.onboarding.phase === "revoked" || expected.onboarding.grant_status === "revoked")
      )) || state.coreRevoked;
      if (result && result.transaction) state.revokeTransaction = validateRevokeTransaction(result, expected);
      const resultHash = hash(result && result.transaction_hash);
      if (resultHash) state.revokeTxHash = resultHash;
      state.revokePrepared = !!state.revokeTransaction || !!state.revokeTxHash;
      if (state.revokeTxHash) {
        const input = $("revoke-hash");
        if (input) input.value = state.revokeTxHash;
        addTxLink("tx-links", state.revokeTxHash);
      }
      text("core-revoked", state.coreRevoked ? "Core 已撤销" : "Core 状态待确认");
      setMessage(state.revokeTxHash
        ? "Core 已撤销；原链上撤销哈希可继续独立复验。"
        : state.revokePrepared
          ? "Core 已先行处理撤销；点击链上撤销后再独立复验。"
          : "Core 撤销已处理，暂无待发送的链上交易。",
      );
      return result;
    });
  }

  async function verifyRevokeAction(hashValue = state.revokeTxHash) {
      const txHash = hash(hashValue || $("revoke-hash") && $("revoke-hash").value);
      if (!txHash) throw new Error("revoke hash invalid");
      state.revokeTxHash = txHash;
      state.chainRevoked = false;
      let result;
      try {
        result = await request("/api/revoke/verify", "POST", {transaction_hash: txHash});
      } catch (error) {
        if (!error || error.status !== 409) throw error;
        try {
          await refreshStatus();
        } catch (_) {
          state.revokeTxHash = txHash;
          setMessage("链上撤销状态暂不可用；已保留原交易哈希，请重试复验。");
          render();
          return null;
        }
        if (state.revokeTxHash) {
          setMessage("Core 已撤销；链上仍待复验，已保留原交易哈希，请重试同一验证。");
        } else {
          setMessage("链上撤销证据已拒绝；请输入正确交易哈希后重新复验。");
        }
        render();
        return null;
      }
      state.chainRevoked = !!(result && (result.verified === true || result.status === "verified"));
      const rejected = !!(result && result.status === "rejected");
      if (rejected) {
        state.revokeRejectedTxHash = hash(result.rejected_transaction_hash) || txHash;
        state.revokeTxHash = null;
        state.revokePrepared = false;
        state.revokeTransaction = null;
        const input = $("revoke-hash");
        if (input) input.value = "";
      }
      addTxLink("tx-links", txHash);
      text("chain-revoked", state.chainRevoked ? "链上撤销已确认" : "链上撤销待确认");
      setMessage(state.chainRevoked
        ? "Core 与链上撤销均已独立确认。"
        : rejected
          ? "链上撤销证据已拒绝；请输入正确交易哈希后重新复验。"
          : "Core 已撤销；链上仍待复验，请重试同一交易。",
      );
      return result;
  }

  async function verifyRevoke(hashValue = state.revokeTxHash) {
    return run(() => verifyRevokeAction(hashValue));
  }

  async function sendRevoke() {
    return run(async () => {
      if (!state.revokeTransaction) throw new Error("revoke preparation required");
      await ensureWalletReady();
      const txHash = hash(await providerRequest("eth_sendTransaction", [state.revokeTransaction]));
      if (!txHash) throw new Error("revoke hash invalid");
      state.revokeTxHash = txHash;
      state.revokePrepared = true;
      const hashInput = $("revoke-hash");
      if (hashInput) hashInput.value = txHash;
      addTxLink("tx-links", txHash);
      render();
      const result = await verifyRevokeAction(txHash);
      // As with allowance, preserve and submit the returned hash before any
      // post-send wallet readiness check can observe an account/chain change.
      await ensureWalletReady();
      return result;
    });
  }

  function addTxLink(id, value) {
    const txHash = hash(value);
    const node = $(id);
    if (!node || !txHash) return;
    const link = document.createElement("a");
    link.href = `${EXPLORER_TX}${txHash}`;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = txHash;
    node.replaceChildren(link);
  }

  function render() {
    const current = state.status;
    if (current) {
      text("owner", current.owner);
      text("chain", current.chain_id);
      text("mode", current.mode);
      text("token", current.token);
      text("executor", current.executor);
      text("payee", current.payee);
      text("balance", field(current, "balance", "token_balance", "token_balance_display"));
      text("phase", state.phase);
      const terms = current.terms || {};
      text("terms", `总额 ${field(terms, "total") || "—"} · 单笔 ${field(terms, "per_payment") || "—"} · 服务 ${field(terms, "price") || "—"} TestUSD`);
      const commerce = current.commerce;
      const commerceNode = $("commerce");
      if (commerceNode) {
        const settlementSummary = purchaseSettlementSummary();
        const commerceText = commerce ? JSON.stringify(commerce, null, 2) : "暂无订单快照";
        commerceNode.textContent = settlementSummary ? `${settlementSummary}\n\n${commerceText}` : commerceText;
      }
    }
    text("wallet-address", state.account || "未连接");
    text("wallet-chain", state.chainId || "未连接");
    text("allowance-status", state.allowanceVerified ? "已复验" : state.allowanceVerificationPending ? "等待复验" : "未授权");
    text("core-revoked", state.coreRevoked ? "Core 已撤销" : "Core 未撤销");
    text("chain-revoked", state.chainRevoked ? "链上撤销已确认" : "链上撤销待确认");
    const connectNode = $("connect");
    if (connectNode) connectNode.disabled = state.connected;
    const switchNode = $("switch-network");
    if (switchNode) switchNode.disabled = !state.provider && !provider();
    const phase = state.phase;
    const action = (id, disabled) => { const node = $(id); if (node) node.disabled = !!disabled; };
    const allowanceHash = $("allowance-hash");
    action("wallet-verify", !state.connected || phase !== "wallet");
    action("grant-verify", !state.connected || phase !== "core_grant");
    action("budget-bind", !state.connected || phase !== "budget_grant");
    action("allowance-approve", !state.connected || phase !== "allowance" || !!state.allowanceTxHash);
    const enteredAllowanceHash = allowanceHash && hash(allowanceHash.value);
    // Keep verification explicitly retryable for a known hash, including
    // after a wallet event or a reload; this never initiates another send.
    action("allowance-verify", !(state.allowanceTxHash || enteredAllowanceHash));
    action("preview", !state.connected || phase !== "ready" || !!state.previewId);
    action("execute", !state.previewId || !!state.purchaseState && ["delivered", "paid_but_undelivered"].includes(state.purchaseState));
    const purchaseInput = $("purchase-id");
    const purchaseId = purchaseInput && purchaseInput.value ? purchaseInput.value.trim() : state.purchaseId;
    action("purchase-query", !purchaseId);
    action("purchase-recover", !purchaseId);
    const revokeHash = $("revoke-hash");
    const enteredRevokeHash = revokeHash && hash(revokeHash.value);
    const canPrepareRevoke = phase === "ready" || state.coreRevoked;
    action("revoke-prepare", !state.connected || !canPrepareRevoke || state.revokePrepared);
    action("revoke-chain", !state.connected || !state.revokePrepared || !!state.revokeTxHash);
    action("revoke-verify", !(state.revokeTxHash || enteredRevokeHash) || state.chainRevoked);
    if (allowanceHash && state.allowanceTxHash && !allowanceHash.value) allowanceHash.value = state.allowanceTxHash;
    if (revokeHash && state.revokeTxHash && !revokeHash.value) revokeHash.value = state.revokeTxHash;
    if (state.purchaseSettlement) {
      addTxLink("tx-links", state.purchaseSettlement.transaction_hash);
    } else {
      if (state.allowanceTxHash) addTxLink("tx-links", state.allowanceTxHash);
      if (state.revokeTxHash) addTxLink("tx-links", state.revokeTxHash);
    }
  }

  function bindDom() {
    const handlers = {
      connect,
      "switch-network": switchNetwork,
      "wallet-verify": verifyWallet,
      "grant-verify": verifyGrant,
      "budget-bind": bindBudget,
      "allowance-approve": approveAllowance,
      "allowance-verify": () => verifyAllowance(),
      preview,
      execute: executePurchase,
      "purchase-query": queryPurchase,
      "purchase-recover": recoverPurchase,
      "revoke-prepare": prepareRevoke,
      "revoke-chain": sendRevoke,
      "revoke-verify": () => verifyRevoke(),
    };
    for (const [id, handler] of Object.entries(handlers)) {
      const node = $(id);
      if (node) node.onclick = () => handler();
    }
    const allowanceHash = $("allowance-hash");
    if (allowanceHash) allowanceHash.oninput = () => render();
    const revokeHash = $("revoke-hash");
    if (revokeHash) revokeHash.oninput = () => render();
    const purchaseInput = $("purchase-id");
    if (purchaseInput) purchaseInput.oninput = () => render();
  }

  async function init() {
    if (!state.initPromise) {
      state.initPromise = (async () => {
        bindProvider(provider());
        bindDom();
        await ensureSession();
        await refreshStatus();
        setMessage("请连接配置的 OKX owner 钱包；所有签名和交易都需要明确点击。");
        render();
      })().catch((error) => {
        setMessage(friendlyError(error));
        render();
        return null;
      });
    }
    return state.initPromise;
  }

  const publicApi = {
    init,
    connect,
    switchNetwork,
    verifyWallet,
    verifyGrant,
    bindBudget,
    approveAllowance,
    verifyAllowance,
    preview,
    execute: executePurchase,
    queryPurchase,
    recoverPurchase,
    prepareRevoke,
    sendRevoke,
    verifyRevoke,
    validateTypedData,
    validateAllowanceTransaction,
    validateRevokeTransaction,
    state: () => ({
      phase: state.phase,
      connected: state.connected,
      account: state.account,
      chain_id: state.chainId,
      allowance_tx_hash: state.allowanceTxHash,
      allowance_verification_pending: state.allowanceVerificationPending,
      allowance_verified: state.allowanceVerified,
      allowance_rejected_tx_hash: state.allowanceRejectedTxHash,
      preview_id: state.previewId,
      purchase_id: state.purchaseId,
      purchase_state: state.purchaseState,
      core_revoked: state.coreRevoked,
      revoke_prepared: state.revokePrepared,
      revoke_tx_hash: state.revokeTxHash,
      revoke_rejected_tx_hash: state.revokeRejectedTxHash,
      chain_revoked: state.chainRevoked,
      message: state.message,
    }),
  };

  globalThis.PublicWalletUI = publicApi;
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => { void init(); });
    else void init();
  }
})();
