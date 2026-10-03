"""Process-isolated real Marketplace + HTTP merchant + local-chain Core."""
from pathlib import Path
import json
import sys
import socket

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'apps/marketplace'))
from examples.monad_commerce.runtime import BudgetCommerceRuntime

METHODS={'search','details','preview','execute','purchase','snapshot','revoke'}

def main():
    runtime=None
    try:
        for line in sys.stdin:
            if len(line)>1048576: return
            request=json.loads(line)
            try:
                method=request['method']; args=request.get('arguments',{})
                if method=='initialize' and runtime is None:
                    with socket.socket() as sock:
                        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
                    runtime=BudgetCommerceRuntime(Path(sys.argv[1]),port,args)
                    runtime.__enter__()
                    result={'status':'ready','mode':'local_anvil'}
                elif runtime is not None and method in METHODS:
                    result=getattr(runtime,method)(**args)
                else: raise ValueError('operation not allowed')
                response={'id':request['id'],'result':result}
            except Exception as exc:
                response={'id':request.get('id'),'error':str(exc)}
            print(json.dumps(response,default=str),flush=True)
    finally:
        if runtime is not None: runtime.__exit__(None,None,None)
if __name__=='__main__': main()
