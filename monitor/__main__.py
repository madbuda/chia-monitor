import asyncio
import json
import logging
from asyncio.queues import Queue

import colorlog
from chia.util.config import load_config
from chia.util.default_root import DEFAULT_ROOT_PATH

from monitor.collectors import RpcCollector, WsCollector
from monitor.database import ChiaEvent, async_session
from monitor.exporter import ChiaExporter
from monitor.notifier import Notifier

chia_config = load_config(DEFAULT_ROOT_PATH, "config.yaml")


def initilize_logging():
    handler = colorlog.StreamHandler()
    log_date_format = "%Y-%m-%dT%H:%M:%S"
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(asctime)s.%(msecs)03d %(log_color)s%(levelname)-6s%(reset)s %(message)s",
            datefmt=log_date_format,
            reset=True,
        ))
    logger = colorlog.getLogger()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


async def persist_event(event: ChiaEvent):
    async with async_session.begin() as db_session:
        db_session.add(event)
        await db_session.commit()


async def aggregator(exporter: ChiaExporter, notifier: Notifier) -> None:
    rpc_collector = None
    ws_collector = None
    event_queue = Queue()

    try:
        logging.info("🔌 Creating RPC Collector...")
        rpc_collector = await RpcCollector.create(DEFAULT_ROOT_PATH, chia_config, event_queue)
    except Exception as e:
        logging.warning(f"Failed to create RPC collector. Continuing without it. {e}")

    try:
        logging.info("🔌 Creating WebSocket Collector...")
        ws_collector = await WsCollector.create(DEFAULT_ROOT_PATH, chia_config, event_queue)
    except Exception as e:
        logging.warning(f"Failed to create WebSocket collector. Continuing without it. {e}")

    if rpc_collector and ws_collector:
        logging.info("🚀 Starting monitoring loop!")
        asyncio.create_task(rpc_collector.task())
        asyncio.create_task(ws_collector.task())
        asyncio.create_task(notifier.task())
        while True:
            try:
                event = await event_queue.get()
                exporter.process_event(event)
                await persist_event(event)

            except asyncio.CancelledError:
                break
    else:
        logging.error("Failed to create any collector.")

    logging.info("🛑 Shutting down!")
    if rpc_collector:
        await rpc_collector.close()
    if ws_collector:
        await ws_collector.close()


if __name__ == "__main__":
    initilize_logging()

    with open("config.json") as f:
        config = json.load(f)

    status_url = config["notifications"]["status_service_url"]
    alert_url = config["notifications"]["alert_service_url"]
    status_interval_minutes = config["notifications"]["status_interval_minutes"]
    lost_plots_alert_threshold = config["notifications"]["lost_plots_alert_threshold"]

    notifier = Notifier(status_url, alert_url, status_interval_minutes, lost_plots_alert_threshold)
    exporter = ChiaExporter()

    try:
        asyncio.run(aggregator(exporter, notifier))
    except KeyboardInterrupt:
        logging.info("👋 Bye!")
