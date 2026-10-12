import asyncio
import copy
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from utils import UserError, timezone

log = logging.getLogger(__name__)
STATES = {"scheduled", "sending", "sent", "failed", "cancelled", "missed"}


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("不正なデータ構造")
    if data.get("version") != 1:
        raise ValueError("未対応のデータバージョン")
    for key in ("guilds", "users", "schedules"):
        if not isinstance(data[key], dict):
            raise ValueError("不正なデータ構造")
    for guild in data["guilds"].values():
        timezone(guild["timezone"])
    for user in data["users"].values():
        validate_minutes(user["notifications"])
    for sid, record in data["schedules"].items():
        if record["id"] != sid or record["status"] not in STATES:
            raise ValueError("不正な予約")
        for key in ("guild_id", "channel_id", "author_id"):
            if not str(record[key]).isdigit():
                raise ValueError("不正なDiscord ID")
        for key in ("scheduled_at", "created_at"):
            if datetime.fromisoformat(record[key]).utcoffset() is None:
                raise ValueError("UTCオフセットが必要です")
        if not isinstance(record["content"], str) or not record["content"].strip():
            raise ValueError("本文が空です")
        if len(record["content"]) > 4000 or type(record["allow_everyone"]) is not bool:
            raise ValueError("不正な本文またはメンション設定")
        if type(record["revision"]) is not int or record["revision"] < 0:
            raise ValueError("不正な更新番号")
        validate_minutes(record["notifications"])
        validate_minutes(record["notified"])
        if not set(record["notified"]).issubset(record["notifications"]):
            raise ValueError("不正な通知履歴")
        if not isinstance(record["message_ids"], list) or any(not str(mid).isdigit() for mid in record["message_ids"]):
            raise ValueError("不正なメッセージID一覧")


def validate_minutes(values):
    if not isinstance(values, list) or any(type(v) is not int or v <= 0 for v in values):
        raise ValueError("不正な通知時間")
    if len(values) != len(set(values)):
        raise ValueError("通知時間が重複しています")


class Storage:
    def __init__(self, path):
        self.path = Path(path)
        self.backup = self.path.with_suffix(self.path.suffix + ".bak")
        self.lock = asyncio.Lock()
        self.data = {"version": 1, "guilds": {}, "users": {}, "schedules": {}}
        self.process_lock = None

    def acquire(self):
        """同じJSONを複数プロセスが使うことをOSロックで禁止する。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.process_lock = open(str(self.path) + ".lock", "a+b")
        self.process_lock.seek(0)
        self.process_lock.write(b"0")
        self.process_lock.flush()
        self.process_lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.process_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.process_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.process_lock.close()
            self.process_lock = None
            raise RuntimeError("同じ保存先を使うBotが既に動作しています。") from None

    def close(self):
        if self.process_lock:
            self.process_lock.close()
            self.process_lock = None

    @staticmethod
    def atomic(path, payload):
        temporary = Path(str(path) + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def load(self):
        self.acquire()
        if not self.path.exists() and not self.backup.exists():
            self.atomic(self.path, json.dumps(self.data))
            return
        for source in (self.path, self.backup):
            try:
                candidate = json.loads(source.read_text(encoding="utf-8"))
                validate(candidate)
            except (OSError, ValueError, KeyError, TypeError, UserError):
                log.exception("JSON読み込みエラー: %s", source)
                continue
            if source == self.backup:
                # 古いバックアップには送信前の状態が残る可能性がある。
                # 自動再送せず保留することで重複を防ぐ。
                for record in candidate["schedules"].values():
                    if record["status"] in {"scheduled", "sending"}:
                        record.update(status="failed", error="バックアップ復旧のため送信状況の確認が必要です。")
                self.atomic(self.path, json.dumps(candidate, ensure_ascii=False, indent=2))
                log.warning("バックアップから復旧。未完了予約は重複防止のため失敗扱いです。")
            self.data = candidate
            return
        raise RuntimeError("JSONとバックアップを読み込めません。上書きせず起動を中止します。")

    def snapshot(self):
        return copy.deepcopy(self.data)

    def persist(self, candidate):
        validate(candidate)
        self.atomic(self.backup, json.dumps(self.data, ensure_ascii=False, indent=2))
        self.atomic(self.path, json.dumps(candidate, ensure_ascii=False, indent=2))

    async def change(self, operation):
        async with self.lock:
            candidate = self.snapshot()
            result = operation(candidate)
            task = asyncio.create_task(asyncio.to_thread(self.persist, candidate))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                self.data = candidate
                raise
            except OSError:
                log.exception("JSON保存エラー")
                raise UserError("保存できませんでした。空き容量や保存先の権限を管理者に確認してください。") from None
            self.data = candidate
            return copy.deepcopy(result)
