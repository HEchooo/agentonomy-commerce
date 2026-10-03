const $ = id => document.getElementById(id);
let previewId=sessionStorage.getItem('budgetPreview'), running=false, requestId=null;
let submitted=!!previewId&&sessionStorage.getItem('budgetSubmitted')==='true', terminal=false;
let staleSession=false;
function controls(){
  $('csv').readOnly=!!previewId||running;
  $('preview').disabled=running||!!previewId;
  $('execute').disabled=running||!previewId||submitted||staleSession;
  $('retry').disabled=running||!previewId||!submitted||staleSession;
  $('new').disabled=running||(submitted&&!terminal&&!staleSession);
  $('new').textContent=staleSession?'重置本地会话':'编辑新订单';
}
async function api(path,body){
  const response=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
  const value=await response.json();
  if(!response.ok){const error=new Error(typeof value.detail==='string'?value.detail:'请求未完成，请保留当前订单后重试。');error.status=response.status;throw error;}
  return value;
}
async function refresh(){
  const s=await api('/api/status');
  $('remaining').textContent=s.remaining_amount_usdc;$('used').textContent=s.used_amount_usdc;$('reserved').textContent=s.reserved_amount_usdc;
  $('grant').textContent=s.grant_status==='active'?'有效':s.grant_status;
  $('meter').style.width=`${Math.max(0,Math.min(100,Number(s.remaining_amount_usdc)*100))}%`;
  $('revoke').disabled=running||s.grant_status!=='active';
}
async function act(fn){
  if(running)return;running=true;controls();$('revoke').disabled=true;
  try{await fn();}catch(e){$('message').textContent=e.message;}
  finally{running=false;controls();await refresh().catch(()=>{});}
}
$('preview').onclick=()=>act(async()=>{
  requestId=requestId||crypto.randomUUID();
  const p=await api('/api/preview',{csv_text:$('csv').value,idempotency_key:requestId});
  previewId=p.preview_id;sessionStorage.setItem('budgetPreview',previewId);sessionStorage.setItem('budgetCsv',$('csv').value);
  $('message').textContent='输入与报价已固定：0.30 TestUSD。确认购买将执行本地链付款。';
});
function markSessionMissing(){
  staleSession=true;
  $('state').textContent='本地会话已过期';$('evidence').replaceChildren();
  $('result').textContent='本地订单记录已清理。请重置当前会话后重新创建报价。';
  $('message').textContent='当前订单引用已失效。请点击“重置本地会话”；不会自动付款。';
  controls();
}
async function execute(){
  submitted=true;sessionStorage.setItem('budgetSubmitted','true');controls();
  let p;
  try{p=await api('/api/execute',{preview_id:previewId});}
  catch(error){if(error.status===404){markSessionMissing();return;}throw error;}
  terminal=p.state==='delivered'||p.state==='unpaid_terminal'||p.state==='confirmation_required'||(p.state==='failed'&&p.reason_code==='PAYMENT_REVERTED'&&p.settlement?.status==='reverted');
  const names={failed:'执行未完成',delivered:'已交付',payment_submitted:'付款等待复验',paid_but_undelivered:'已付款，交付待恢复',payment_settled_delivery_failed:'已付款，交付待恢复',unpaid_terminal:'已确认未付款',confirmation_required:'需要新的授权'};
  $('state').textContent=names[p.state]||p.state;$('evidence').replaceChildren();
  for(const [key,value] of [['订单',p.purchase_id],['付款交易',p.settlement?.transaction_hash],['付款已复验',p.settlement?.status==='verified'?'是':p.settlement?.status==='reverted'?'已确认失败':'等待'],['输出摘要',p.output_hash]]){
    if(!value)continue;const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=value;row.append(dt,dd);$('evidence').append(row);
  }
  $('result').textContent=p.service_result?JSON.stringify(p.service_result,null,2):'请查询／恢复当前订单。';
  $('message').textContent=p.state==='delivered'?'报告已交付。重复查询这个订单不会再次扣款。':'当前订单尚未交付。请使用“查询／恢复同一订单”。';
}
$('execute').onclick=()=>act(execute);$('retry').onclick=()=>act(execute);
function resetLocalSession(){
  if(running||(submitted&&!terminal&&!staleSession))return;
  previewId=null;requestId=null;submitted=false;terminal=false;staleSession=false;sessionStorage.removeItem('budgetPreview');sessionStorage.removeItem('budgetSubmitted');sessionStorage.removeItem('budgetCsv');controls();
  $('message').textContent='可以编辑下一份输入。每份新订单会单独计费。';
}
$('new').onclick=resetLocalSession;
$('revoke').onclick=()=>act(async()=>{
  if(!confirm('撤销后将不能进行新的采购，已完成付款不受影响。继续？'))return;
  const value=await api('/api/revoke',{});$('revoke-status').textContent=`Core 已撤销；链上撤销${value.chain_revoked?'已确认':'待确认'}。`;
});
if(previewId&&sessionStorage.getItem('budgetCsv'))$('csv').value=sessionStorage.getItem('budgetCsv');
if(previewId)$('state').textContent=submitted?'待查询原订单':'报价已恢复';
controls();if(previewId)$('message').textContent=submitted?'检测到本次会话的原订单，请查询／恢复，不要重复创建采购。':'已恢复报价；尚未确认购买，付款前请核对。';
refresh().catch(e=>{$('message').textContent=e.message;});
