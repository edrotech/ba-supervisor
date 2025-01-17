"""Tests for resolution manager."""
import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import attr
import pytest

from supervisor.coresys import CoreSys
from supervisor.exceptions import ResolutionError
from supervisor.resolution.const import (
    ContextType,
    IssueType,
    SuggestionType,
    UnhealthyReason,
    UnsupportedReason,
)
from supervisor.resolution.data import Issue, Suggestion


def test_properies_unsupported(coresys: CoreSys):
    """Test resolution manager properties unsupported."""
    assert coresys.core.supported

    coresys.resolution.unsupported = UnsupportedReason.OS
    assert not coresys.core.supported


def test_properies_unhealthy(coresys: CoreSys):
    """Test resolution manager properties unhealthy."""
    assert coresys.core.healthy

    coresys.resolution.unhealthy = UnhealthyReason.SUPERVISOR
    assert not coresys.core.healthy


@pytest.mark.asyncio
async def test_resolution_dismiss_suggestion(coresys: CoreSys):
    """Test resolution manager suggestion apply api."""
    coresys.resolution.suggestions = clear_backup = Suggestion(
        SuggestionType.CLEAR_FULL_BACKUP, ContextType.SYSTEM
    )

    assert SuggestionType.CLEAR_FULL_BACKUP == coresys.resolution.suggestions[-1].type
    coresys.resolution.dismiss_suggestion(clear_backup)
    assert clear_backup not in coresys.resolution.suggestions

    with pytest.raises(ResolutionError):
        coresys.resolution.dismiss_suggestion(clear_backup)


@pytest.mark.asyncio
async def test_resolution_apply_suggestion(coresys: CoreSys):
    """Test resolution manager suggestion apply api."""
    coresys.resolution.suggestions = clear_backup = Suggestion(
        SuggestionType.CLEAR_FULL_BACKUP, ContextType.SYSTEM
    )
    coresys.resolution.suggestions = create_backup = Suggestion(
        SuggestionType.CREATE_FULL_BACKUP, ContextType.SYSTEM
    )

    mock_backups = AsyncMock()
    mock_health = AsyncMock()
    coresys.backups.do_backup_full = mock_backups
    coresys.resolution.healthcheck = mock_health

    await coresys.resolution.apply_suggestion(clear_backup)
    await coresys.resolution.apply_suggestion(create_backup)

    assert mock_backups.called
    assert mock_health.called

    assert clear_backup not in coresys.resolution.suggestions
    assert create_backup not in coresys.resolution.suggestions

    with pytest.raises(ResolutionError):
        await coresys.resolution.apply_suggestion(clear_backup)


@pytest.mark.asyncio
async def test_resolution_dismiss_issue(coresys: CoreSys):
    """Test resolution manager issue apply api."""
    coresys.resolution.issues = updated_failed = Issue(
        IssueType.UPDATE_FAILED, ContextType.SYSTEM
    )

    assert IssueType.UPDATE_FAILED == coresys.resolution.issues[-1].type
    coresys.resolution.dismiss_issue(updated_failed)
    assert updated_failed not in coresys.resolution.issues

    with pytest.raises(ResolutionError):
        coresys.resolution.dismiss_issue(updated_failed)


@pytest.mark.asyncio
async def test_resolution_create_issue_suggestion(coresys: CoreSys):
    """Test resolution manager issue and suggestion."""
    coresys.resolution.create_issue(
        IssueType.UPDATE_ROLLBACK,
        ContextType.CORE,
        "slug",
        [SuggestionType.EXECUTE_REPAIR],
    )

    assert IssueType.UPDATE_ROLLBACK == coresys.resolution.issues[-1].type
    assert ContextType.CORE == coresys.resolution.issues[-1].context
    assert coresys.resolution.issues[-1].reference == "slug"

    assert SuggestionType.EXECUTE_REPAIR == coresys.resolution.suggestions[-1].type
    assert ContextType.CORE == coresys.resolution.suggestions[-1].context


@pytest.mark.asyncio
async def test_resolution_dismiss_unsupported(coresys: CoreSys):
    """Test resolution manager dismiss unsupported reason."""
    coresys.resolution.unsupported = UnsupportedReason.SOFTWARE

    coresys.resolution.dismiss_unsupported(UnsupportedReason.SOFTWARE)
    assert UnsupportedReason.SOFTWARE not in coresys.resolution.unsupported

    with pytest.raises(ResolutionError):
        coresys.resolution.dismiss_unsupported(UnsupportedReason.SOFTWARE)


async def test_suggestions_for_issue(coresys: CoreSys):
    """Test getting suggestions that fix an issue."""
    coresys.resolution.issues = corrupt_repo = Issue(
        IssueType.CORRUPT_REPOSITORY, ContextType.STORE, "test_repo"
    )

    # Unrelated suggestions don't appear
    coresys.resolution.suggestions = Suggestion(
        SuggestionType.EXECUTE_RESET, ContextType.SUPERVISOR
    )
    coresys.resolution.suggestions = Suggestion(
        SuggestionType.EXECUTE_REMOVE, ContextType.STORE, "other_repo"
    )

    assert coresys.resolution.suggestions_for_issue(corrupt_repo) == set()

    # Related suggestions do
    coresys.resolution.suggestions = execute_remove = Suggestion(
        SuggestionType.EXECUTE_REMOVE, ContextType.STORE, "test_repo"
    )
    coresys.resolution.suggestions = execute_reset = Suggestion(
        SuggestionType.EXECUTE_RESET, ContextType.STORE, "test_repo"
    )

    assert coresys.resolution.suggestions_for_issue(corrupt_repo) == {
        execute_reset,
        execute_remove,
    }


async def test_issues_for_suggestion(coresys: CoreSys):
    """Test getting issues fixed by a suggestion."""
    coresys.resolution.suggestions = execute_reset = Suggestion(
        SuggestionType.EXECUTE_RESET, ContextType.STORE, "test_repo"
    )

    # Unrelated issues don't appear
    coresys.resolution.issues = Issue(IssueType.FATAL_ERROR, ContextType.CORE)
    coresys.resolution.issues = Issue(
        IssueType.CORRUPT_REPOSITORY, ContextType.STORE, "other_repo"
    )

    assert coresys.resolution.issues_for_suggestion(execute_reset) == set()

    # Related issues do
    coresys.resolution.issues = fatal_error = Issue(
        IssueType.FATAL_ERROR, ContextType.STORE, "test_repo"
    )
    coresys.resolution.issues = corrupt_repo = Issue(
        IssueType.CORRUPT_REPOSITORY, ContextType.STORE, "test_repo"
    )

    assert coresys.resolution.issues_for_suggestion(execute_reset) == {
        fatal_error,
        corrupt_repo,
    }


def _supervisor_event_message(event: str, data: dict[str, Any]) -> dict[str, Any]:
    """Make mock supervisor event message for ha websocket."""
    return {
        "type": "supervisor/event",
        "data": {
            "event": event,
            "data": data,
        },
    }


async def test_events_on_issue_changes(coresys: CoreSys):
    """Test events fired when an issue changes."""
    with patch.object(
        type(coresys.homeassistant.websocket), "async_send_message"
    ) as send_message:
        # Creating an issue with a suggestion should fire exactly one issue changed event
        assert coresys.resolution.issues == []
        assert coresys.resolution.suggestions == []
        coresys.resolution.create_issue(
            IssueType.CORRUPT_REPOSITORY,
            ContextType.STORE,
            "test_repo",
            [SuggestionType.EXECUTE_RESET],
        )
        await asyncio.sleep(0)

        assert len(coresys.resolution.issues) == 1
        assert len(coresys.resolution.suggestions) == 1
        issue = coresys.resolution.issues[0]
        suggestion = coresys.resolution.suggestions[0]
        send_message.assert_called_once_with(
            _supervisor_event_message("issue_changed", attr.asdict(issue))
        )

        # Adding a suggestion that fixes the issue changes it
        send_message.reset_mock()
        coresys.resolution.suggestions = execute_remove = Suggestion(
            SuggestionType.EXECUTE_REMOVE, ContextType.STORE, "test_repo"
        )
        await asyncio.sleep(0)
        send_message.assert_called_once_with(
            _supervisor_event_message("issue_changed", attr.asdict(issue))
        )

        # Removing a suggestion that fixes the issue changes it again
        send_message.reset_mock()
        coresys.resolution.dismiss_suggestion(execute_remove)
        await asyncio.sleep(0)
        send_message.assert_called_once_with(
            _supervisor_event_message("issue_changed", attr.asdict(issue))
        )

        # Applying a suggestion should only fire an issue removed event
        send_message.reset_mock()
        with patch("shutil.disk_usage", return_value=(42, 42, 2 * (1024.0**3))):
            await coresys.resolution.apply_suggestion(suggestion)

        await asyncio.sleep(0)
        send_message.assert_called_once_with(
            _supervisor_event_message("issue_removed", attr.asdict(issue))
        )


async def test_resolution_apply_suggestion_multiple_copies(coresys: CoreSys):
    """Test resolution manager applies correct suggestion when has multiple that differ by reference."""
    coresys.resolution.suggestions = remove_store_1 = Suggestion(
        SuggestionType.EXECUTE_REMOVE, ContextType.STORE, "repo_1"
    )
    coresys.resolution.suggestions = remove_store_2 = Suggestion(
        SuggestionType.EXECUTE_REMOVE, ContextType.STORE, "repo_2"
    )
    coresys.resolution.suggestions = remove_store_3 = Suggestion(
        SuggestionType.EXECUTE_REMOVE, ContextType.STORE, "repo_3"
    )

    await coresys.resolution.apply_suggestion(remove_store_2)

    assert remove_store_1 in coresys.resolution.suggestions
    assert remove_store_2 not in coresys.resolution.suggestions
    assert remove_store_3 in coresys.resolution.suggestions
