import asyncio
import logging
import re
import secrets
from datetime import datetime

import discord
from discord.ext import tasks

from utils import UserError, local_time, split_message, utcnow

log = logging.getLogger(__name__)


def manager(member):
    return isinstance(member, discord.Member) and (member.guild_permissions.manage_guild or member.guild_permissions.administrator)


class Scheduler:
    def __init__(self, bot, store, grace):
        self.bot, self.store, self.grace = bot, store, grace
        self.recovered = False

    def zone(self, guild_id):
        return self.store.data["guilds"].get(str(guild_id), {}).get("timezone", "Asia/Tokyo")

    def notifications(self, guild_id, author_id):
        key = f"{guild_id}:{author_id}"
        return list(self.store.data["users"].get(key, {}).get("notifications", []))

    def get(self, sid, interaction):
        record = self.store.snapshot()["schedules"].get(sid.upper())
        if (not record or record["guild_id"] != str(interaction.guild_id)
                or (record["author_id"] != str(interaction.user.id) and not manager(interaction.user))):
            raise UserError("予約が見つからないか、この予約を操作する権限がありません。")
        return record

    async def target(self, guild_id, channel_id, author_id):
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            raise UserError("Botが投稿先サーバーに参加していません。")
        try:
            channel = await guild.fetch_channel(int(channel_id))
            author = await guild.fetch_member(int(author_id))
            bot_member = await guild.fetch_member(self.bot.user.id)
        except discord.NotFound:
            raise UserError("投稿先チャンネルまたは予約者がサーバーに見つかりません。") from None
        except discord.HTTPException:
            raise UserError("投稿先の情報を取得できませんでした。Botの権限と接続を確認してください。") from None
        if not isinstance(channel, discord.TextChannel):
            raise UserError("投稿先にはサーバーのテキストチャンネルを指定してください。")
        for member, label in ((author, "予約者"), (bot_member, "Bot")):
            permissions = channel.permissions_for(member)
            if not permissions.view_channel or not permissions.send_messages:
                raise UserError(f"{label}に投稿先チャンネルの閲覧・メッセージ送信権限がありません。")
        return channel, author

    def draft(self, interaction, channel, when, content):
        return dict(id="SCH-" + secrets.token_hex(4).upper(),
                    guild_id=str(interaction.guild_id), channel_id=str(channel.id),
                    author_id=str(interaction.user.id), content=content,
                    scheduled_at=when.isoformat(), created_at=utcnow().isoformat(),
                    notifications=self.notifications(interaction.guild_id, interaction.user.id),
                    notified=[], status="scheduled", message_ids=[], revision=0,
                    allow_everyone=False)

    async def save(self, draft, interaction, original=None):
        channel, author = await self.target(draft["guild_id"], draft["channel_id"], draft["author_id"])
        if not draft["content"].strip():
            raise UserError("投稿内容に空白以外の文字を入力してください。")
        if datetime.fromisoformat(draft["scheduled_at"]) <= utcnow():
            raise UserError("指定された時刻はすでに過ぎています。")
        draft["allow_everyone"] = channel.permissions_for(author).mention_everyone

        def commit(data):
            if original:
                current = data["schedules"].get(original["id"])
                if (not current or current["status"] != "scheduled"
                        or current["revision"] != original["revision"]):
                    raise UserError("予約の状態が変わりました。もう一度詳細を確認してください。")
                self.get(current["id"], interaction)
                draft["revision"] = current["revision"] + 1
                # 通知済みの時間は編集しても再通知しない。
                draft["notified"] = current["notified"]
            elif draft["id"] in data["schedules"]:
                raise UserError("予約IDが重複しました。もう一度予約を作成してください。")
            if datetime.fromisoformat(draft["scheduled_at"]) <= utcnow():
                raise UserError("指定された時刻はすでに過ぎています。")
            data["schedules"][draft["id"]] = dict(draft)
        await self.store.change(commit)
        log.info("予約%s: %s", "変更" if original else "作成", draft["id"])

    async def cancel(self, sid, interaction):
        self.get(sid, interaction)
        def commit(data):
            record = data["schedules"][sid]
            if record["status"] != "scheduled":
                raise UserError("送信開始済みまたは終了済みの予約はキャンセルできません。")
            record.update(status="cancelled", cancelled_at=utcnow().isoformat())
        await self.store.change(commit)
        log.info("予約キャンセル: %s", sid)

    async def dm(self, user_id, text):
        try:
            user = await self.bot.fetch_user(int(user_id))
            await user.send(text, allowed_mentions=discord.AllowedMentions.none())
            return True
        except discord.HTTPException as error:
            log.warning("DM送信失敗 user=%s type=%s", user_id, type(error).__name__)
            return False

    async def recover(self):
        interrupted = []
        def commit(data):
            for record in data["schedules"].values():
                if record["status"] == "sending":
                    record.update(status="failed", error="送信途中でBotが停止しました。投稿先を確認してください。自動再送は行いません。")
                    interrupted.append(dict(record))
        await self.store.change(commit)
        for record in interrupted:
            await self.failure_dm(record)

    async def failure_dm(self, record):
        await self.dm(record["author_id"],
                      f"❌ 予約投稿に失敗しました。\n予約ID：{record['id']}\n"
                      f"投稿先：<#{record['channel_id']}>\n原因：{record.get('error', '不明')}")

    async def update(self, sid, **values):
        await self.store.change(lambda data: data["schedules"][sid].update(values))

    async def dispatch(self, record):
        sid = record["id"]
        try:
            channel, author = await self.target(record["guild_id"], record["channel_id"], record["author_id"])
            permission = channel.permissions_for(author)
            everyone = record["allow_everyone"] and permission.mention_everyone
            roles = await channel.guild.fetch_roles()
            role_ids = set(re.findall(r"<@&(\d+)>", record["content"]))
            allowed_roles = [role for role in roles if str(role.id) in role_ids
                             and (role.mentionable or everyone) and not role.is_default()][:100]
            users = [discord.Object(id=int(uid)) for uid in sorted(set(re.findall(r"<@!?(\d+)>", record["content"])))][:100]
            allowed = discord.AllowedMentions(everyone=everyone, users=users, roles=allowed_roles, replied_user=False)
            ids = list(record["message_ids"])
            for part in split_message(record["content"]):
                message = await channel.send(part, allowed_mentions=allowed)
                ids.append(str(message.id))
                await self.update(sid, message_ids=list(ids))
            await self.update(sid, status="sent", sent_at=utcnow().isoformat())
            log.info("投稿成功: %s", sid)
        except Exception as error:
            log.exception("投稿失敗: %s", sid)
            reason = str(error) if isinstance(error, UserError) else "投稿先へ送信できませんでした。権限や接続を確認してください。一部送信済みの可能性があるため自動再送しません。"
            await self.update(sid, status="failed", error=reason)
            await self.failure_dm(dict(record, error=reason))

    async def tick(self):
        for sid, record in self.store.snapshot()["schedules"].items():
            if record["status"] != "scheduled":
                continue
            seconds = (datetime.fromisoformat(record["scheduled_at"]) - utcnow()).total_seconds()
            if seconds <= 0:
                missed = -seconds > self.grace
                def claim(data):
                    current = data["schedules"][sid]
                    if current["status"] != "scheduled" or current["revision"] != record["revision"]:
                        return None
                    current["status"] = "missed" if missed else "sending"
                    return current
                claimed = await self.store.change(claim)
                if not claimed:
                    continue
                if missed:
                    await self.dm(record["author_id"], f"⚠️ Bot停止・遅延中に予約投稿時刻を過ぎたため、投稿されませんでした。\n予約ID：{sid}")
                else:
                    await self.dispatch(claimed)
                continue
            for minutes in sorted(record["notifications"]):
                if minutes in record["notified"] or seconds > minutes * 60:
                    continue
                def claim_notification(data):
                    current = data["schedules"][sid]
                    if (current["status"] != "scheduled" or current["revision"] != record["revision"]
                            or minutes in current["notified"]):
                        return False
                    # 先に記録し、クラッシュ後の重複DMを避ける。
                    current["notified"].append(minutes)
                    return True
                if await self.store.change(claim_notification):
                    await self.dm(record["author_id"],
                        f"予約投稿のお知らせ\n\n約{max(1, round(seconds / 60))}分後に予約投稿があります。"
                        f"（{minutes}分前通知）\n投稿先：<#{record['channel_id']}>\n"
                        f"投稿日時：{local_time(record['scheduled_at'], self.zone(record['guild_id']))}\n"
                        f"予約ID：{sid}\n\n投稿内容：\n{record['content'][:500]}")
                    # 停止中に複数通知を過ぎていた場合も、各設定は一度だけ処理する。

    @tasks.loop(seconds=1)
    async def loop(self):
        try:
            if not self.recovered:
                await self.recover()
                self.recovered = True
            await self.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("予約監視エラー。次回の監視で継続します。")

    @loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()
