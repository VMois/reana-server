# -*- coding: utf-8 -*-
#
# This file is part of REANA.
# Copyright (C) 2019, 2020, 2021, 2022 CERN.
#
# REANA is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

"""Test command line application."""

import csv
import io
import secrets
import uuid

from click.testing import CliRunner
from reana_db.models import AuditLogAction, User, UserTokenStatus

from reana_server.api_client import WorkflowSubmissionPublisher
from reana_server.reana_admin import reana_admin
from reana_server.reana_admin.consumer import MessageConsumer


def test_export_users(default_user):
    """Test exporting all users as csv."""
    runner = CliRunner()
    expected_csv_file = io.StringIO()
    csv_writer = csv.writer(expected_csv_file, dialect="unix")
    csv_writer.writerow(
        [
            default_user.id_,
            default_user.email,
            default_user.access_token,
            default_user.username,
            default_user.full_name,
        ]
    )
    result = runner.invoke(
        reana_admin, ["user-export", "--admin-access-token", default_user.access_token]
    )
    assert result.output == expected_csv_file.getvalue()


def test_import_users(app, session, default_user):
    """Test importing users from CSV file."""
    runner = CliRunner()
    expected_output = "Users successfully imported."
    users_csv_file_name = "reana-users.csv"
    user_id = uuid.uuid4()
    user_email = "test@reana.io"
    user_access_token = secrets.token_urlsafe(16)
    user_username = "jdoe"
    user_full_name = "John Doe"
    with runner.isolated_filesystem():
        with open(users_csv_file_name, "w") as f:
            csv_writer = csv.writer(f, dialect="unix")
            csv_writer.writerow(
                [user_id, user_email, user_access_token, user_username, user_full_name]
            )

        result = runner.invoke(
            reana_admin,
            [
                "user-import",
                "--admin-access-token",
                default_user.access_token,
                "--file",
                users_csv_file_name,
            ],
        )
        assert expected_output in result.output
        user = session.query(User).filter_by(id_=user_id).first()
        assert user
        assert user.email == user_email
        assert user.access_token == user_access_token
        assert user.username == user_username
        assert user.full_name == user_full_name


def test_grant_token(default_user, session):
    """Test grant access token."""
    runner = CliRunner()

    # non-existing email user
    result = runner.invoke(
        reana_admin,
        [
            "token-grant",
            "--admin-access-token",
            default_user.access_token,
            "-e",
            "nonexisting@example.org",
        ],
    )
    assert "does not exist" in result.output

    # non-existing id user
    result = runner.invoke(
        reana_admin,
        [
            "token-grant",
            "--admin-access-token",
            default_user.access_token,
            "--id",
            "fake_id",
        ],
    )
    assert "does not exist" in result.output

    # non-requested-token user
    user = User(email="johndoe@cern.ch")
    session.add(user)
    session.commit()
    result = runner.invoke(
        reana_admin,
        [
            "token-grant",
            "--admin-access-token",
            default_user.access_token,
            "-e",
            user.email,
        ],
    )
    assert "token status is None, do you want to proceed?" in result.output

    # abort grant
    result = runner.invoke(
        reana_admin,
        [
            "token-grant",
            "--admin-access-token",
            default_user.access_token,
            "-e",
            user.email,
        ],
        input="\n",
    )
    assert "Grant token aborted" in result.output

    # confirm grant
    result = runner.invoke(
        reana_admin,
        [
            "token-grant",
            "--admin-access-token",
            default_user.access_token,
            "-e",
            user.email,
        ],
        input="y\n",
    )
    assert f"Token for user {user.id_} ({user.email}) granted" in result.output
    assert user.access_token
    assert default_user.audit_logs[-1].action is AuditLogAction.grant_token

    # user with active token
    active_user = User(email="active@cern.ch", access_token="valid_token")
    session.add(active_user)
    session.commit()
    result = runner.invoke(
        reana_admin,
        [
            "token-grant",
            "--admin-access-token",
            default_user.access_token,
            "--id",
            str(active_user.id_),
        ],
    )
    assert "has already an active access token" in result.output

    # typical ui user workflow
    ui_user = User(email="ui_user@cern.ch")
    session.add(ui_user)
    session.commit()
    ui_user.request_access_token()
    assert ui_user.access_token_status is UserTokenStatus.requested.name
    assert ui_user.access_token is None
    result = runner.invoke(
        reana_admin,
        [
            "token-grant",
            "--admin-access-token",
            default_user.access_token,
            "--id",
            str(ui_user.id_),
        ],
    )
    assert ui_user.access_token_status is UserTokenStatus.active.name
    assert ui_user.access_token
    assert default_user.audit_logs[-1].action is AuditLogAction.grant_token


def test_revoke_token(default_user, session):
    """Test revoke access token."""
    runner = CliRunner()

    # non-active-token user
    user = User(email="janedoe@cern.ch")
    session.add(user)
    session.commit()
    result = runner.invoke(
        reana_admin,
        [
            "token-revoke",
            "--admin-access-token",
            default_user.access_token,
            "-e",
            user.email,
        ],
    )
    assert "does not have an active access token" in result.output

    # user with requested token
    user.request_access_token()
    assert user.access_token_status == UserTokenStatus.requested.name
    result = runner.invoke(
        reana_admin,
        [
            "token-revoke",
            "--admin-access-token",
            default_user.access_token,
            "-e",
            user.email,
        ],
    )
    assert "does not have an active access token" in result.output

    # user with active token
    user.access_token = "active_token"
    session.commit()
    assert user.access_token
    result = runner.invoke(
        reana_admin,
        [
            "token-revoke",
            "--admin-access-token",
            default_user.access_token,
            "--id",
            str(user.id_),
        ],
    )
    assert "was successfully revoked" in result.output
    assert user.access_token_status == UserTokenStatus.revoked.name
    assert default_user.audit_logs[-1].action is AuditLogAction.revoke_token

    # try to revoke again
    result = runner.invoke(
        reana_admin,
        [
            "token-revoke",
            "--admin-access-token",
            default_user.access_token,
            "--id",
            str(user.id_),
        ],
    )
    assert "does not have an active access token" in result.output


class TestMessageConsumer:
    def test_do_not_remove_message(
        self,
        in_memory_queue_connection,
        default_in_memory_producer,
        consume_queue,
    ):
        """Test if MessageConsumer ignores and re-queues not matching message."""
        workflow_name = "workflow.1"
        queue_name = "workflow-submission"
        consumer = MessageConsumer(
            connection=in_memory_queue_connection,
            queue_name=queue_name,
            key="workflow_id_or_name",
            values_to_delete=["some_other_name"],
        )
        in_memory_wsp = WorkflowSubmissionPublisher(
            connection=in_memory_queue_connection
        )

        in_memory_wsp.publish_workflow_submission("1", workflow_name, {})
        consume_queue(consumer, limit=1)
        assert not in_memory_queue_connection.channel().queues[queue_name].empty()
        in_memory_queue_connection.channel().queues.clear()

    def test_removes_message(
        self,
        in_memory_queue_connection,
        default_in_memory_producer,
        consume_queue,
    ):
        """Test if MessageConsumer correctly removes specified message."""
        workflow_name = "workflow.1"
        consumer = MessageConsumer(
            connection=in_memory_queue_connection,
            queue_name="workflow-submission",
            key="workflow_id_or_name",
            values_to_delete=[workflow_name],
        )
        in_memory_wsp = WorkflowSubmissionPublisher(
            connection=in_memory_queue_connection
        )

        in_memory_wsp.publish_workflow_submission("1", workflow_name, {})
        consume_queue(consumer, limit=1)
        assert (
            in_memory_queue_connection.channel().queues["workflow-submission"].empty()
        )
        in_memory_queue_connection.channel().queues.clear()
