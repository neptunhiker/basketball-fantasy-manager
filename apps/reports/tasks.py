"""Background tasks run by the Django-Q cluster."""

from . import services


def generate_report(report_id):
    """One report of a run (`services.run_report`), queued by `services.enqueue`."""
    report = services.run_report(report_id)
    if report is None:
        return "skipped: the report no longer exists"
    return f"{report.get_kind_display()}: {report.status}, ${report.cost_usd}"
