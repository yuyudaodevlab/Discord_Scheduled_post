import logging
from datetime import datetime

import discord
from discord import app_commands

from scheduler import manager
from utils import UserError, local_time, parse_time, split_message, timezone, utcnow

log = logging.getLogger(__name__)
STATUS = {"scheduled": "予約中", "sending": "送信中", "sent": "投稿済み",
          "failed": "失敗・要確認", "cancelled": "キャンセル済み", "missed": "期限超過"}


async def tell(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def handle_error(interaction, error):
    error = getattr(error, "original", error)
    if isinstance(error, UserError):
        message = str(error)
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = f"連続送信を防ぐため、あと{max(1, round(error.retry_after))}秒待ってお試しください。"
    elif isinstance(error, app_commands.CheckFailure):
        message = "このコマンドを実行する権限がありません。サーバー内で実行してください。"
    else:
        log.error("操作エラー", exc_info=(type(error), error, error.__traceback__))
        message = "操作を完了できませんでした。少し待ってもう一度お試しください。"
    try:
        await tell(interaction, "❌ " + message)
    except discord.HTTPException:
        log.warning("操作エラーの応答を送信できませんでした。")


def detail(service, record, preview=False):
    zone = service.zone(record["guild_id"])
    stamp = int(datetime.fromisoformat(record["scheduled_at"]).timestamp())
    embed = discord.Embed(title="予約内容の確認" if preview else "予約の詳細", color=0x5865F2)
    embed.add_field(name="予約ID", value=record["id"])
    embed.add_field(name="投稿先", value=f"<#{record['channel_id']}>")
    embed.add_field(name="作成者", value=f"<@{record['author_id']}>")
    embed.add_field(name="投稿予定日時", value=f"{local_time(record['scheduled_at'], zone)} ({zone})\n<t:{stamp}:F> / <t:{stamp}:R>", inline=False)
    embed.add_field(name="作成日時", value=local_time(record["created_at"], zone))
    embed.add_field(name="状態", value=STATUS[record["status"]])
    embed.add_field(name="DM事前通知", value=", ".join(f"{m}分前" for m in record["notifications"]) or "なし", inline=False)
    # 詳細では本文全文、確認では冒頭のみ。Embed合計6000文字以内。
    embed.description = record["content"][:(1000 if preview else 4000)]
    count = len(split_message(record["content"]))
    notes = [f"この投稿は長いため、{count}メッセージに分割して送信されます。"] if count > 1 else []
    if preview:
        notes.append("確認画面は3分で無効になります。本文は入力どおり保存します。")
        if len(record["content"]) > 1000:
            notes.append("プレビューは冒頭1000文字です。")
    if record.get("error"):
        embed.add_field(name="原因", value=record["error"][:1000], inline=False)
    if record["message_ids"]:
        embed.add_field(name="送信済みメッセージ", value="\n".join(
            f"[メッセージ {index + 1}](https://discord.com/channels/{record['guild_id']}/{record['channel_id']}/{mid})"
            for index, mid in enumerate(record["message_ids"])), inline=False)
    embed.set_footer(text="\n".join(notes) or "日時はサーバー設定、Discord Timestampは閲覧端末の設定で表示されます。")
    return embed


class OwnerView(discord.ui.View):
    def __init__(self, owner):
        super().__init__(timeout=180)
        self.owner = owner
        self.message = None
        self.busy = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner:
            await tell(interaction, "❌ この画面は操作した本人だけが使用できます。")
            return False
        if self.busy:
            await tell(interaction, "⚠️ この操作は処理中または終了済みです。")
            return False
        return True

    async def on_error(self, interaction, error, item):
        self.busy = False
        await handle_error(interaction, error)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def finish(self, interaction, text):
        self.busy = True
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(content=text, view=self, embed=None)
        self.stop()


async def send_view(interaction, view, **kwargs):
    view.message = await interaction.followup.send(ephemeral=True, view=view, wait=True,
        allowed_mentions=discord.AllowedMentions.none(), **kwargs)


class Confirm(OwnerView):
    def __init__(self, service, owner, record, original=None, cancel=False):
        super().__init__(owner)
        self.service, self.record, self.original, self.cancel = service, record, original, cancel
        if cancel:
            self.accept.label = "キャンセルを確定"
            self.accept.style = discord.ButtonStyle.danger
            self.reject.label = "戻る"
        elif original:
            self.accept.label = "変更を保存"

    @discord.ui.button(label="予約する", style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        self.busy = True
        await interaction.response.defer(ephemeral=True)
        if self.cancel:
            await self.service.cancel(self.record["id"], interaction)
            text = "✅ 予約をキャンセルしました。"
        else:
            await self.service.save(self.record, interaction, self.original)
            text = f"✅ 予約{'を変更' if self.original else 'を登録'}しました。ID：{self.record['id']}"
        await self.finish(interaction, text)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def reject(self, interaction, button):
        self.busy = True
        await interaction.response.defer(ephemeral=True)
        await self.finish(interaction, "変更せず終了しました。")


class PostModal(discord.ui.Modal):
    def __init__(self, service, interaction, channel, when, original=None):
        super().__init__(title="予約投稿を編集" if original else "予約投稿を作成", timeout=300)
        self.service, self.channel, self.when, self.original = service, channel, when, original
        self.owner = interaction.user.id
        if original:
            self.time_input = discord.ui.TextInput(label="投稿日時（相対時間も指定可能）", max_length=80,
                default=datetime.fromisoformat(original["scheduled_at"]).astimezone(timezone(service.zone(interaction.guild_id))).strftime("%Y-%m-%d %H:%M:%S"))
            self.add_item(self.time_input)
        self.body = discord.ui.TextInput(label="投稿内容", style=discord.TextStyle.paragraph,
            max_length=4000, default=original["content"] if original else None)
        self.add_item(self.body)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.user.id != self.owner:
            raise UserError("この画面は作成者だけが使用できます。")
        when = parse_time(str(self.time_input), self.service.zone(interaction.guild_id)) if self.original else self.when
        if when <= utcnow():
            raise UserError("指定された時刻はすでに過ぎています。")
        if not str(self.body).strip():
            raise UserError("投稿内容に空白以外の文字を入力してください。")
        await self.service.target(interaction.guild_id, self.channel.id,
                                  self.original["author_id"] if self.original else interaction.user.id)
        if self.original:
            record = dict(self.original, content=str(self.body), channel_id=str(self.channel.id), scheduled_at=when.isoformat())
        else:
            record = self.service.draft(interaction, self.channel, when, str(self.body))
        view = Confirm(self.service, interaction.user.id, record, self.original)
        await send_view(interaction, view, embed=detail(self.service, record, preview=True))

    async def on_error(self, interaction, error):
        await handle_error(interaction, error)


class Pages(OwnerView):
    def __init__(self, service, interaction):
        super().__init__(interaction.user.id)
        self.service, self.guild_id, self.page = service, str(interaction.guild_id), 0

    def render(self):
        records = [r for r in self.service.store.snapshot()["schedules"].values()
                   if r["guild_id"] == self.guild_id and r["author_id"] == str(self.owner)]
        records.sort(key=lambda r: r["scheduled_at"], reverse=True)
        pages = max(1, (len(records) + 4) // 5)
        self.page = min(self.page, pages - 1)
        self.previous.disabled, self.next.disabled = self.page == 0, self.page >= pages - 1
        embed = discord.Embed(title="自分の予約一覧", color=0x5865F2)
        for record in records[self.page * 5:self.page * 5 + 5]:
            embed.add_field(name=f"{record['id']} · {STATUS[record['status']]}",
                value=f"{local_time(record['scheduled_at'], self.service.zone(self.guild_id))}\n"
                      f"<#{record['channel_id']}>\n{record['content'][:120]}", inline=False)
        if not records:
            embed.description = "予約はまだありません。/schedule-post で作成できます。"
        embed.set_footer(text=f"{self.page + 1}/{pages}ページ · /schedule-show で詳細を確認")
        return embed

    @discord.ui.button(label="前へ")
    async def previous(self, interaction, button):
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(embed=self.render(), view=self)

    @discord.ui.button(label="次へ")
    async def next(self, interaction, button):
        self.page += 1
        await interaction.response.edit_message(embed=self.render(), view=self)


def install(tree, service):
    @tree.command(name="schedule-post", description="日時と投稿先を指定して予約投稿を作成します")
    @app_commands.guild_only()
    @app_commands.describe(time="例：2026-10-30 21:00 / 30秒後 / 10m", channel="投稿先のテキストチャンネル")
    async def post(interaction: discord.Interaction, time: str, channel: discord.TextChannel):
        when = parse_time(time, service.zone(interaction.guild_id))
        # Modalは3秒以内に開く必要があるため、ここではキャッシュを使う。
        for member in (interaction.user, interaction.guild.me):
            permissions = channel.permissions_for(member)
            if not permissions.view_channel or not permissions.send_messages:
                raise UserError("予約者またはBotに投稿先の閲覧・送信権限がありません。")
        await interaction.response.send_modal(PostModal(service, interaction, channel, when))

    @tree.command(name="schedule-list", description="自分の予約一覧を表示します")
    @app_commands.guild_only()
    async def listing(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        view = Pages(service, interaction)
        await send_view(interaction, view, embed=view.render())

    @tree.command(name="schedule-show", description="予約の本文・日時・状態を表示します")
    @app_commands.guild_only()
    async def show(interaction: discord.Interaction, reservation_id: str):
        await interaction.response.send_message(embed=detail(service, service.get(reservation_id, interaction)),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name="schedule-cancel", description="確認してから予約をキャンセルします")
    @app_commands.guild_only()
    async def cancel(interaction: discord.Interaction, reservation_id: str):
        record = service.get(reservation_id, interaction)
        if record["status"] != "scheduled":
            raise UserError("予約中の投稿だけキャンセルできます。")
        await interaction.response.defer(ephemeral=True)
        await send_view(interaction, Confirm(service, interaction.user.id, record, cancel=True),
                        content=f"⚠️ {record['id']} を本当にキャンセルしますか？")

    @tree.command(name="schedule-edit", description="予約の日時・本文を編集します。channelで投稿先も変更できます")
    @app_commands.guild_only()
    async def edit(interaction: discord.Interaction, reservation_id: str, channel: discord.TextChannel | None = None):
        record = service.get(reservation_id, interaction)
        if record["status"] != "scheduled":
            raise UserError("予約中の投稿だけ編集できます。")
        target = channel or interaction.guild.get_channel(int(record["channel_id"]))
        if not isinstance(target, discord.TextChannel):
            raise UserError("投稿先が見つかりません。channelで新しい投稿先を選んでください。")
        await interaction.response.send_modal(PostModal(service, interaction, target, None, record))

    async def autocomplete(interaction: discord.Interaction, current: str):
        records = service.store.snapshot()["schedules"].values()
        return [app_commands.Choice(name=f"{r['id']} · {STATUS[r['status']]} · {r['content'][:30]}", value=r["id"])
                for r in records if r["guild_id"] == str(interaction.guild_id)
                and (r["author_id"] == str(interaction.user.id) or manager(interaction.user))
                and current.upper() in r["id"]][:25]
    for command in (show, edit, cancel):
        command.autocomplete("reservation_id")(autocomplete)

    @tree.command(name="schedule-timezone", description="サーバーの予約日時に使うタイムゾーンを変更します")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_timezone(interaction: discord.Interaction, timezone: str):
        from utils import timezone as check_timezone
        check_timezone(timezone)
        await interaction.response.defer(ephemeral=True)
        await service.store.change(lambda data: data["guilds"].setdefault(str(interaction.guild_id), {}).update(timezone=timezone))
        await tell(interaction, f"✅ タイムゾーンを {timezone} に変更しました。既存予約の投稿時刻は変わりません。")

    @tree.command(name="schedule-botname", description="このサーバー内でのBotのニックネームを変更します")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def botname(interaction: discord.Interaction, name: app_commands.Range[str, 1, 32]):
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.guild.me.edit(nick=name)
        except discord.Forbidden:
            raise UserError("Botにニックネームを変更する権限がありません。") from None
        await tell(interaction, "✅ このサーバー内でのBotのニックネームを変更しました。")

    group = app_commands.Group(name="schedule-notification", description="自分のDM事前通知を設定します", guild_only=True)

    async def test_dm(interaction):
        if not await service.dm(interaction.user.id, "予約投稿BotからDM通知を送信できることを確認しました。"):
            raise UserError("DMを送信できませんでした。サーバーメンバーからのDMを許可しているか確認してください。")

    async def modify_notifications(interaction, minutes=None, action="add"):
        def commit(data):
            key = f"{interaction.guild_id}:{interaction.user.id}"
            values = data["users"].setdefault(key, {"notifications": []})["notifications"]
            if action == "add":
                if minutes in values:
                    raise UserError("その通知時間は登録済みです。")
                if len(values) >= 20:
                    raise UserError("通知時間は最大20件までです。")
                values.append(minutes)
                values.sort(reverse=True)
            elif action == "remove":
                if minutes not in values:
                    raise UserError("その通知時間は登録されていません。")
                values.remove(minutes)
            else:
                values.clear()
        await service.store.change(commit)
        await tell(interaction, "✅ 通知設定を保存しました。これから作成する予約に適用されます。既存予約の通知設定は変わりません。")

    @group.command(name="add", description="DM送信を確認して事前通知を追加します（今後の予約に適用）")
    @app_commands.checks.cooldown(1, 10, key=lambda i: (i.guild_id, i.user.id))
    async def add(interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 525600]):
        if minutes in service.notifications(interaction.guild_id, interaction.user.id):
            raise UserError("その通知時間は登録済みです。")
        await interaction.response.defer(ephemeral=True)
        await test_dm(interaction)
        await modify_notifications(interaction, minutes)

    @group.command(name="remove", description="事前通知の時間を削除します")
    async def remove(interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 525600]):
        await interaction.response.defer(ephemeral=True)
        await modify_notifications(interaction, minutes, "remove")

    @group.command(name="list", description="自分の事前通知設定を表示します")
    async def notifications_list(interaction: discord.Interaction):
        values = service.notifications(interaction.guild_id, interaction.user.id)
        await tell(interaction, "DM事前通知：" + (", ".join(f"{m}分前" for m in values) or "なし"))

    @group.command(name="clear", description="今後作成する予約の事前通知をすべて解除します")
    async def clear(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await modify_notifications(interaction, action="clear")

    @group.command(name="test", description="テストDMを送信します")
    @app_commands.checks.cooldown(1, 10, key=lambda i: (i.guild_id, i.user.id))
    async def test(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await test_dm(interaction)
        await tell(interaction, "✅ テストDMを送信しました。")

    tree.add_command(group)
    tree.error(handle_error)
