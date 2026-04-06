"""
ContentIQ — Azure Function: Timer Trigger
Fires every Monday at 9:00 AM (UTC) and runs the Substack scraper.

DEPLOYED TO AZURE: This file is the entry point that Azure Functions reads on deployment.
Azure scans it for trigger decorators (@app.timer_trigger), registers the function by its
Python name (substack_weekly_ingest), and fires it on the defined cron schedule automatically.
Without this file, Azure would have no instructions on when or what to run.
"""

import azure.functions as func
import logging

from substack_scraper import run

app = func.FunctionApp()

@app.timer_trigger(
    schedule="0 0 9 * * 1",   # every Monday at 09:00 UTC
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def substack_weekly_ingest(timer: func.TimerRequest) -> None:
    logging.info("ContentIQ Substack scheduler fired.")

    if timer.past_due:
        logging.warning("Timer is past due — running now to catch up.")

    run(dry_run=False, reset=False)

    logging.info("Substack ingestion complete.")
