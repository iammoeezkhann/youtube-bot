from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from bot.config import AppConfig

logger = logging.getLogger(__name__)


def start_scheduler(config: AppConfig, job) -> None:
    scheduler = BlockingScheduler()

    if config.schedule.type == "daily_times":
        for index, value in enumerate(config.schedule.times):
            hour, minute = (int(part) for part in value.split(":", 1))
            trigger = CronTrigger(
                hour=hour,
                minute=minute,
                timezone=config.schedule.timezone,
            )
            scheduler.add_job(
                job,
                trigger=trigger,
                id=f"youtube_upload_{index}",
                max_instances=1,
            )
        times_label = ", ".join(config.schedule.times)
        logger.info(
            "Scheduler started: %s uploads/day at %s (%s)",
            len(config.schedule.times),
            times_label,
            config.schedule.timezone,
        )
    elif config.schedule.type == "cron":
        trigger = CronTrigger.from_crontab(
            config.schedule.cron,
            timezone=config.schedule.timezone,
        )
        label = f"cron ({config.schedule.cron}, {config.schedule.timezone})"
        scheduler.add_job(job, trigger=trigger, id="youtube_upload", max_instances=1)
        logger.info("Scheduler started: %s", label)
    else:
        trigger = IntervalTrigger(
            hours=max(config.schedule.hours, 0),
            minutes=max(config.schedule.minutes, 0),
        )
        label = f"every {config.schedule.hours}h {config.schedule.minutes}m"
        scheduler.add_job(job, trigger=trigger, id="youtube_upload", max_instances=1)
        logger.info("Scheduler started: %s", label)

    scheduler.start()
