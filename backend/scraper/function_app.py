"""
ContentIQ — Azure Function: Timer Trigger
Fires every Monday at 9:00 AM (UTC) and runs the Substack scraper.
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
