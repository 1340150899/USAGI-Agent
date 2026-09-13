"""Single-instance durable HTTP application entry point."""
import argparse
import asyncio
import json
import os
from pathlib import Path

import uvicorn
from usagi_agent.observability import configure_service_logging
from .api import create_app
from .bootstrap import build_server
from .service import ApplicationService
from .store import AppStore


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args=parser.parse_args()
    settings=json.loads(Path(args.config).read_text(encoding="utf-8"))
    log_dir=settings.get("log_dir")
    configure_service_logging(
        "http-server",
        ("usagi_httpserver", "uvicorn", "uvicorn.error", "uvicorn.access"),
        log_root=log_dir,
    )
    configure_service_logging("agent-framework", ("usagi_agent",), log_root=log_dir)
    import logging
    logging.getLogger("usagi_httpserver").info("http_server_initialization_started")
    data=Path(settings.get("data_dir",".usagi/http")).resolve()
    data.mkdir(parents=True,exist_ok=True)
    # Keep one worker owner per deployment; SQLite still serializes Store CAS.
    lock=(data/"server.lock").open("a+b")
    if os.name=="nt":
        import msvcrt
        lock.seek(0);lock.write(b"0");lock.flush();lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    credentials={}
    for binding in settings.pop("bindings"):
        token=os.environ[binding.pop("token_env")]
        if token in credentials:raise ValueError("credential reused across bindings")
        credentials[token]=binding
    settings["credentials"]=credentials
    settings["resume_hmac_key"]=os.environ[settings.pop("resume_key_env","USAGI_RESUME_KEY")]
    settings["data_dir"]=str(data)
    if settings.get("weixin_adapter"):
        adapter=settings["weixin_adapter"]
        adapter["token"]=os.environ[adapter.pop("token_env")]
    server=await build_server(xhs_url=settings.get("xhs_url"),settings=settings)
    logging.getLogger("usagi_httpserver").info(
        "http_server_agent_initialized ready=%s", server.ready
    )
    stores={
        "dev":AppStore(data/"application-dev.db"),
        "debug":AppStore(data/"application-debug.db"),
    }
    environment=settings.get("database_environment","dev")
    if environment not in stores:
        raise ValueError("database_environment must be dev or debug")
    service=ApplicationService(server,stores[environment],settings)
    app=create_app(server,credentials={k:v["principal"] for k,v in credentials.items()},
                   scenario_key="example.research_writer",service=service)
    try:
        await uvicorn.Server(uvicorn.Config(app,host=settings.get("host","127.0.0.1"),
                                           port=settings.get("port",8080))).serve()
    finally:
        logging.getLogger("usagi_httpserver").info("http_server_shutdown_complete")
        lock.close()


if __name__=="__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        import logging
        logging.getLogger("usagi_httpserver").exception(
            "http_server_fatal error_type=%s", type(exc).__name__
        )
        raise
