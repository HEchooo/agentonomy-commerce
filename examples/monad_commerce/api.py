"""Single-user local rehearsal UI. Binds loopback only, never a public wallet server."""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
import uvicorn

from examples.commerce.node import public_result
from examples.monad_commerce.rehearsal import LocalRehearsal, public_purchase

STATIC = Path(__file__).with_name("web")

class Preview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv_text: str = Field(min_length=1,max_length=131072)
    idempotency_key: str = Field(min_length=1,max_length=160)

class Execute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=1,max_length=160)


def create_app(*, origin="http://127.0.0.1:8090"):
    parsed = urlsplit(origin)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("this launcher supports only local loopback origin")
    @asynccontextmanager
    async def lifespan(app):
        with LocalRehearsal() as runtime:
            app.state.runtime = runtime
            yield
    app = FastAPI(title="Agentonomy local budget commerce",lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

    @app.middleware("http")
    async def scope(request: Request, next_handler):
        if request.headers.get("host") != parsed.netloc:
            return JSONResponse({"error":"invalid_host"},status_code=403)
        if request.method not in {"GET","HEAD"} and request.headers.get("origin") != origin:
            return JSONResponse({"error":"origin_required"},status_code=403)
        if request.method == "POST":
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 262144:
                    return JSONResponse({"error":"body_too_large"},status_code=413)
            request._body = bytes(body)
        response = await next_handler(request)
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/")
    def home(): return FileResponse(STATIC/"index.html")
    @app.get("/app.js")
    def javascript(): return FileResponse(STATIC/"app.js",media_type="text/javascript")
    @app.get("/app.css")
    def stylesheet(): return FileResponse(STATIC/"app.css",media_type="text/css")
    @app.get("/api/status")
    def status(): return app.state.runtime.request("snapshot")
    @app.post("/api/preview")
    def preview(body: Preview):
        try:
            value = app.state.runtime.request("preview",dict(offering_id="csv-reconciliation-v1",**body.model_dump()))
            return public_result("create_clink_purchase_preview",value)
        except (ValueError, RuntimeError):
            raise HTTPException(400,"请检查 CSV 格式；相同请求编号不能替换输入。") from None
    @app.post("/api/execute")
    def execute(body: Execute):
        try: return app.state.runtime.execute(body.preview_id)
        except KeyError:
            raise HTTPException(404,"本地订单不存在或会话已过期。") from None
        except (ValueError, RuntimeError):
            raise HTTPException(409,"购买尚未确认，请保留当前订单并查询状态。") from None
    @app.get("/api/purchases/{purchase_id}")
    def purchase(purchase_id: str):
        try: return public_purchase(app.state.runtime.request("purchase",{"purchase_id":purchase_id}))
        except KeyError: raise HTTPException(404,"订单不存在") from None
        except (ValueError, RuntimeError): raise HTTPException(404,"订单不存在") from None
    @app.post("/api/revoke")
    def revoke(): return app.state.runtime.revoke()
    return app

def main():
    uvicorn.run(create_app(),host="127.0.0.1",port=8090,access_log=False)

if __name__ == "__main__": main()
