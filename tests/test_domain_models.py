import pytest
from pydantic import ValidationError

from agentic_workshop.domain.identity import EmployeeId, TaskId
from agentic_workshop.domain.tasks import WorkTask


def test_task_rejects_self_dependency() -> None:
    task_id = TaskId("task-1")
    with pytest.raises(ValidationError):
        WorkTask(
            id=task_id,
            title="Publish report",
            description="Publish the approved report",
            requester_id=EmployeeId("editor"),
            dependency_ids=(task_id,),
        )


def test_domain_models_are_immutable() -> None:
    task = WorkTask(
        id=TaskId("task-1"),
        title="Publish report",
        description="Publish the approved report",
        requester_id=None,
    )
    with pytest.raises(ValidationError):
        task.title = "Changed"  # type: ignore[misc]
