from app.db.models.task import TaskStatus, is_valid_transition


def test_phase_zero_transitions_are_enforced():
    assert is_valid_transition(TaskStatus.CREATED, TaskStatus.QUEUED)
    assert is_valid_transition(TaskStatus.CREATED, TaskStatus.CANCELLED)
    assert is_valid_transition(TaskStatus.RUNNING, TaskStatus.SUCCESS)
    assert not is_valid_transition(TaskStatus.CREATED, TaskStatus.SUCCESS)
    assert not is_valid_transition(TaskStatus.SUCCESS, TaskStatus.RUNNING)
