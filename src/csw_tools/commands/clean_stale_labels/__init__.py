"""Remove old static labels for workloads no longer observed by CSW."""

from csw_tools.commands.clean_stale_labels.command import command

__all__ = ["command"]
