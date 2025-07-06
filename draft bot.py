import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import json
import os
import datetime
import random
import string
import asyncio
from discord.ui import Modal, TextInput
from discord import TextStyle
import re


load_dotenv()
EMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

MIDDLEMAN_ROLE_ID = 1374569721185173594
DRAFT_CATEGORY_ID = 1383335152650027108
LOG_CHANNEL_ID = 1377074782167761027
DRAFTS_FILE = "drafts.json"

# Track pending payments
pending_payments = {}  # channel_id: {cash_tag: user_id}
# Track how much each user has paid
payments_received = {}  # channel_id: {user_id: float}


if not os.path.exists(DRAFTS_FILE):
    with open(DRAFTS_FILE, "w") as f:
        json.dump({}, f)

def load_drafts():
    with open(DRAFTS_FILE, "r") as f:
        return json.load(f)

# ✅ NOW OUTSIDE OF load_drafts
async def move_and_delete_voice_channels(guild, draft):
    target_channel = guild.get_channel(1377124001519898634)
    if not target_channel:
        print("❌ Target voice channel not found")
        return

    for key in ("vc1_id", "vc2_id"):
        vc_id = draft.get(key)
        if vc_id:
            vc = guild.get_channel(vc_id)
            if vc:
                for member in vc.members:
                    try:
                        await member.move_to(target_channel)
                    except Exception as e:
                        print(f"Error moving {member.display_name}: {e}")
                try:
                    await vc.delete()
                except Exception as e:
                    print(f"Error deleting VC {vc.name}: {e}")


async def update_live_queue(channel):
    data = load_drafts()
    draft = data.get(str(channel.id))
    if not draft:
        return

    live_msg_id = draft.get("live_queue_message_id")
    if not live_msg_id:
        return

    try:
        message = await channel.fetch_message(live_msg_id)
    except:
        return

    queue_ids = draft["players"]
    mentions = [f"<@{uid}>" for uid in queue_ids]
    desc = "\n".join(mentions) if mentions else "No players yet."

    embed = discord.Embed(
        title="📋 Current Players in Queue",
        description=desc,
        color=discord.Color.blue()
    )
    await message.edit(embed=embed)


async def update_queue_embed(channel, draft, queue_message_id):
    try:
        queue_message = await channel.fetch_message(queue_message_id)
    except discord.NotFound:
        print("Queue message not found.")
        return

    player_mentions = [f"<@{uid}>" for uid in draft["players"]]
    description = "\n".join(player_mentions) if player_mentions else "No players in queue yet."

    embed = discord.Embed(
        title="🎯 Current Players in Queue",
        description=description,
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"Queue: {len(draft['players'])}/{len(draft['players']) + 2} • Made by blur.exe")

    await queue_message.edit(embed=embed)

def save_drafts(data):
    with open(DRAFTS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def disable_all_buttons(view: discord.ui.View):
    for item in view.children:
        if isinstance(item, discord.ui.Button):
            item.disabled = True


async def disable_queue_buttons(channel):
    """Disable join/leave buttons once the draft starts."""
    data = load_drafts()
    draft = data.get(str(channel.id))
    if not draft:
        return
    message_id = draft.get("queue_message_id")
    if not message_id:
        return
    try:
        msg = await channel.fetch_message(message_id)
        team_count = int(draft["team_size"][0])
        view = DraftQueueView(channel.id, team_count * 2)
        disable_all_buttons(view)
        await msg.edit(view=view)
    except Exception as e:
        print(f"Error disabling queue buttons: {e}")


def generate_final_teams_embed(draft):
    embed = discord.Embed(
        title="📋 Final Teams",
        description="All picks are complete. Here are the final teams:",
        color=discord.Color.green()
    )

    c1_id = draft["captains"]["team1"]
    c2_id = draft["captains"]["team2"]
    t1 = draft["team1"]
    t2 = draft["team2"]

    team1_mentions = [f"<@{uid}>" for uid in t1]
    team2_mentions = [f"<@{uid}>" for uid in t2]

    embed.add_field(
        name="🟦 Team 1",
        value=f"**Captain:** <@{c1_id}>\n" + "\n".join(team1_mentions) if team1_mentions else "No picks.",
        inline=True
    )
    embed.add_field(
        name="🟥 Team 2",
        value=f"**Captain:** <@{c2_id}>\n" + "\n".join(team2_mentions) if team2_mentions else "No picks.",
        inline=True
    )

    embed.set_footer(text="Made by blur.exe")
    return embed

class ManualStartView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @discord.ui.button(label="Manual Start", style=discord.ButtonStyle.danger)
    async def manual_start(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_drafts()
        draft = data.get(str(self.channel_id))

        if not draft:
            await interaction.response.send_message("Draft not found.", ephemeral=True)
            return

        # Only the middleman can click this
        if interaction.user.id != draft.get("middleman_id"):
            await interaction.response.send_message("Only the selected middle man can use this.", ephemeral=True)
            return

        await interaction.response.send_message("⏩ Manually starting the draft...", ephemeral=True)
        await auto_start_draft(interaction.guild, interaction.channel, skip_middleman=True)


class MiddleManForm(discord.ui.Modal, title="Middle Man Setup"):
    cashapp = discord.ui.TextInput(label="Enter your Cash App username", placeholder="e.g. johndoe", required=True)

    def __init__(self, channel_id):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        data = load_drafts()
        draft = data.get(str(self.channel_id), {})

        draft["middleman"] = {
            "id": interaction.user.id,
            "cashapp": self.cashapp.value
        }
        save_drafts(data)

        await interaction.response.send_message(
            f"✅ You are now the Middle Man.\nYour Cash App: `{self.cashapp.value}`", ephemeral=True
        )

        await begin_cashapp_collection(interaction.guild, interaction.channel)

class MiddleManButton(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @discord.ui.button(label="I'm the Middle Man", style=discord.ButtonStyle.primary)
    async def confirm_mm(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Optional: permission check
        if MIDDLEMAN_ROLE_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("You don't have permission to be the Middle Man.", ephemeral=True)
            return

        data = load_drafts()
        draft = data.get(str(self.channel_id))
        if draft is not None:
            draft["middleman_id"] = interaction.user.id
            save_drafts(data)

        # ✅ Send the modal to collect the Cash App username
        await interaction.response.send_modal(MiddlemanCashTagModal(self.channel_id))



class CashAppSubmitView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @discord.ui.button(label="Submit Cash App Username", style=discord.ButtonStyle.green)
    async def submit_tag(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_drafts()
        draft = data.get(str(self.channel_id))
        if not draft:
            await interaction.response.send_message("Draft not found.", ephemeral=True)
            return

        if interaction.user.id not in draft.get("players", []):
            await interaction.response.send_message(
                "❌ Only players in the draft queue can submit a Cash App username.",
                ephemeral=True,
            )
            return

        already = draft.get("player_tags", {}).get(str(interaction.user.id))
        if already:
            await interaction.response.send_message("You've already submitted your username.", ephemeral=True)
            return

        await interaction.response.send_modal(PlayerCashTagForm(self.channel_id))




class PlayerCashTagForm(discord.ui.Modal, title="Enter Your Cash App Username"):
    def __init__(self, channel_id):
        super().__init__()
        self.channel_id = channel_id
        self.cashapp = discord.ui.TextInput(
            label="**Cash App USERNAME** (not $tag)",
            placeholder="e.g. johndoe",
            required=True,
        )
        self.add_item(self.cashapp)

    async def on_submit(self, interaction: discord.Interaction):
        data = load_drafts()
        draft = data.get(str(self.channel_id), {})

        if "player_tags" not in draft:
            draft["player_tags"] = {}

        if interaction.user.id not in draft.get("players", []):
            await interaction.response.send_message(
                "❌ You are not in this draft's queue.", ephemeral=True
            )
            return

        tag = self.cashapp.value.strip().lower()
        existing = [t.lower() for t in draft.get("player_tags", {}).values()]
        if tag in existing:
            await interaction.response.send_message(
                "❌ That Cash App username is already used by another player.",
                ephemeral=True,
            )
            return
        if len(tag) <= 3:
            await interaction.response.send_message(
                "❌ Cash App username must be longer than 3 characters.", ephemeral=True
            )
            return

        if str(interaction.user.id) in draft["player_tags"]:
            await interaction.response.send_message(
                "You already submitted your Cash App username.", ephemeral=True
            )
            return

        draft["player_tags"][str(interaction.user.id)] = tag
        save_drafts(data)

        await interaction.response.send_message("✅ Cash App username submitted!", ephemeral=True)

        channel = interaction.client.get_channel(self.channel_id)
        if channel:
            await channel.send(f"📝 {interaction.user.mention} has submitted their Cash App username.")

        if draft:
            if len(draft.get("player_tags", {})) == len(draft.get("players", [])):
                await send_payment_instructions(channel)
            else:
                print(
                    f"{len(draft['player_tags'])}/{len(draft['players'])} Cash Tags submitted"
                )



class PaymentControlView(discord.ui.View):
    def __init__(self, channel_id, *, double: bool = False):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.double = double

    @discord.ui.button(label="Start Draft Manually", style=discord.ButtonStyle.red)
    async def manual_start(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_drafts()
        draft = data.get(str(self.channel_id))

        if not draft:
            await interaction.response.send_message("Draft not found.", ephemeral=True)
            return

        if interaction.user.id != draft.get("middleman_id"):
            await interaction.response.send_message("Only the selected middle man can start the draft.", ephemeral=True)
            return

        await interaction.response.send_message("✅ Starting manually...", ephemeral=True)
        if self.double:
            # For double or nothing just mark it complete and announce
            data = load_drafts()
            draft = data.get(str(self.channel_id), {})
            if "double" in draft:
                draft["double"]["state"] = "complete"
            save_drafts(data)
            await interaction.channel.send("@everyone Double or Nothing started manually!")
        else:
            await auto_start_draft(interaction.guild, interaction.channel, skip_middleman=True)


class DoublePromptView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.message: discord.Message | None = None

    @discord.ui.button(label="Double", style=discord.ButtonStyle.green)
    async def double(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_drafts()
        draft = data.get(str(self.channel_id))
        if not draft:
            await interaction.response.send_message("Draft not found.", ephemeral=True)
            return

        if interaction.user.id not in draft.get("players", []):
            await interaction.response.send_message("You are not part of this draft.", ephemeral=True)
            return

        double = draft.setdefault("double", {"state": "voting", "votes": []})
        if interaction.user.id not in double["votes"]:
            double["votes"].append(interaction.user.id)
            # Start countdown when the first vote is cast
            if len(double["votes"]) == 1:
                double["expires"] = int(datetime.datetime.utcnow().timestamp()) + 60
                save_drafts(data)
                asyncio.create_task(update_double_countdown(interaction.channel, self.message.id))
            else:
                save_drafts(data)

        total = len(draft.get("players", []))
        embed = self.message.embeds[0]
        if len(double["votes"]) == total:
            double["state"] = "loser_select"
            save_drafts(data)
            disable_all_buttons(self)
            await self.message.edit(view=self)
            embed.description = "All players agreed! Select the losing team."
            await self.message.edit(embed=embed)
            await interaction.response.defer()
            await send_loser_selection(interaction.channel)
        else:
            embed.description = (
                f"If the losing team wants a rematch for double the stakes, click **Double**.\n"
                f"Votes: {len(double['votes'])}/{total}\n"
                "Otherwise click **Close Draft**."
            )
            if len(double["votes"]) == 1:
                embed.set_footer(text="Time remaining: 60s")
            await self.message.edit(embed=embed)
            await interaction.response.defer()

    @discord.ui.button(label="Close Draft", style=discord.ButtonStyle.red)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        disable_all_buttons(self)
        if self.message:
            await self.message.edit(view=self)
        await interaction.response.send_message("Draft closed. Use /enddraft to log results.")


class LoserButtonView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.message: discord.Message | None = None

    async def _process_vote(self, interaction: discord.Interaction, team: str):
        data = load_drafts()
        draft = data.get(str(self.channel_id))
        if not draft:
            await interaction.response.send_message("Draft not found.", ephemeral=True)
            return

        captains = [draft.get("captains", {}).get("team1"), draft.get("captains", {}).get("team2")]
        if interaction.user.id not in captains:
            await interaction.response.send_message("Only team captains can choose the losing team.", ephemeral=True)
            return

        double = draft.setdefault("double", {})
        votes = double.setdefault("loser_votes", {})
        votes[str(interaction.user.id)] = team
        save_drafts(data)

        embed = self.message.embeds[0]

        if len(votes) < 2:
            embed.description = "Vote recorded. Waiting for the other captain..."
            await self.message.edit(embed=embed)
            await interaction.response.send_message("Vote recorded.", ephemeral=True)
            return

        values = list(votes.values())
        if values[0] != values[1]:
            double["loser_votes"] = {}
            save_drafts(data)
            embed.description = "❌ Captains selected different teams. Please agree on the loser."
            await self.message.edit(embed=embed)
            await interaction.response.send_message("Votes differed.", ephemeral=True)
            return

        # Both captains agree
        double["state"] = "collecting"
        double["loser_team"] = values[0]
        double["loser_votes"] = {}
        save_drafts(data)

        if values[0] == "team1":
            players = [draft["captains"]["team1"]] + draft.get("team1", [])
        else:
            players = [draft["captains"]["team2"]] + draft.get("team2", [])

        embed.description = "Collecting double payment from the losing team..."
        await self._disable_buttons()
        await self.message.edit(embed=embed)
        await interaction.response.defer()
        await send_payment_instructions(interaction.channel, players=players, double=True)

    async def _disable_buttons(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            await self.message.edit(view=self)

    @discord.ui.button(label="Team 1", style=discord.ButtonStyle.blurple)
    async def team1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._process_vote(interaction, "team1")

    @discord.ui.button(label="Team 2", style=discord.ButtonStyle.red)
    async def team2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._process_vote(interaction, "team2")


async def send_double_prompt(channel):
    data = load_drafts()
    draft = data.get(str(channel.id))
    if not draft:
        return

    draft["double"] = {"state": "voting", "votes": []}
    save_drafts(data)

    embed = discord.Embed(
        title="Double or Nothing?",
        description=(
            "If the losing team wants a rematch for double the stakes, click **Double**.\n"
            "Otherwise click **Close Draft**."
        ),
        color=discord.Color.purple(),
    )
    view = DoublePromptView(channel.id)
    msg = await channel.send(embed=embed, view=view)
    view.message = msg

    double = draft.setdefault("double", {})
    double["message_id"] = msg.id
    save_drafts(data)



async def update_double_countdown(channel: discord.TextChannel, message_id: int):
    while True:
        await asyncio.sleep(5)
        data = load_drafts()
        draft = data.get(str(channel.id), {})
        double = draft.get("double", {})
        if double.get("message_id") != message_id or double.get("state") != "voting":
            return

        expires = double.get("expires", int(datetime.datetime.utcnow().timestamp()))
        remaining = expires - int(datetime.datetime.utcnow().timestamp())
        if remaining <= 0:
            try:
                msg = await channel.fetch_message(message_id)
            except Exception:
                return

            embed = msg.embeds[0]
            embed.description = (
                "If the losing team wants a rematch for double the stakes, click **Double**.\n"
                "Otherwise click **Close Draft**."
            )
            embed.set_footer(text="")
            await msg.edit(embed=embed)

            double.update({"state": "voting", "votes": []})
            double.pop("expires", None)
            save_drafts(data)
            return
        else:
            try:
                msg = await channel.fetch_message(message_id)
            except Exception:
                return
            embed = msg.embeds[0]
            embed.set_footer(text=f"Time remaining: {remaining}s")
            await msg.edit(embed=embed)


async def send_loser_selection(channel):
    data = load_drafts()
    draft = data.get(str(channel.id))
    if not draft:
        return

    t1_members = [draft["captains"]["team1"]] + draft.get("team1", [])
    t2_members = [draft["captains"]["team2"]] + draft.get("team2", [])

    def names(ids):
        members = [channel.guild.get_member(uid) for uid in ids]
        return ", ".join(m.display_name for m in members if m)

    desc = f"Team 1: {names(t1_members)}\nTeam 2: {names(t2_members)}"

    embed = discord.Embed(
        title="Who lost the match?",
        description=desc,
        color=discord.Color.orange(),
    )
    view = LoserButtonView(channel.id)
    msg = await channel.send(embed=embed, view=view)
    view.message = msg

    double = draft.setdefault("double", {})
    double["loser_message_id"] = msg.id
    save_drafts(data)



class PickButton(discord.ui.Button):
    def __init__(self, player, channel_id):
        super().__init__(label=player.display_name, style=discord.ButtonStyle.primary)
        self.player = player
        self.channel_id = str(channel_id)

    async def callback(self, interaction: discord.Interaction):
        data = load_drafts()
        draft = data.get(self.channel_id)
        if not draft:
            await interaction.response.send_message("Draft not found for this channel.", ephemeral=True)
            return

        turn = draft["pick_turn"]
        expected_id = draft["captains"][turn]
        if interaction.user.id != expected_id:
            await interaction.response.send_message("❌ It's not your turn to pick.", ephemeral=True)
            return

        draft[turn].append(self.player.id)
        draft["available"].remove(self.player.id)

        if draft["snake_draft"]:
            total_picks = len(draft["team1"]) + len(draft["team2"])
            total_slots = len(draft["players"]) - 2
            pattern = []
            while len(pattern) < total_slots:
                pattern += ["team1", "team2", "team2", "team1"]
            draft["pick_turn"] = pattern[total_picks] if total_picks < len(pattern) else "team1"
        else:
            draft["pick_turn"] = "team2" if turn == "team1" else "team1"

        save_drafts(data)
        await interaction.response.send_message(f"{self.player.mention} picked by {interaction.user.mention}!", ephemeral=False)
        await send_pick_options(interaction.channel)

async def begin_cashapp_collection(guild, channel):
    data = load_drafts()
    draft = data.get(str(channel.id))
    if not draft:
        return

    middleman = f"<@{draft.get('middleman_id')}>" if draft.get('middleman_id') else "Middleman"
    player_mentions = " ".join(f"<@{uid}>" for uid in draft.get("players", []))

    embed = discord.Embed(
        title="💵 Payment Username Collection",
        description=(
            f"{middleman} will hold the pot.\n"
            f"{player_mentions}\n"
            "Click below and enter your **Cash App USERNAME** (not $tag)."
        ),
        color=discord.Color.green(),
    )

    # Show an image explaining username vs tag
    info_embed = discord.Embed(title="Cash App Username vs Tag")
    info_embed.set_image(url="https://ibb.co/mVbGrQWX")
    info_msg = await channel.send(embed=info_embed)

    draft["cashapp_info_msg_id"] = info_msg.id
    save_drafts(data)

    view = CashAppSubmitView(channel_id=channel.id)
    manual_view = ManualStartView(channel_id=channel.id)

    await channel.send(embed=embed, view=view)
    await channel.send(view=manual_view)

async def send_middleman_selection(channel):
    data = load_drafts()
    draft = data.get(str(channel.id))
    if not draft:
        return

    embed = discord.Embed(
        title="⏳ Waiting for Middle Man",
        description="Click the button below if you're the Middleman.\nYou'll be prompted to enter your Cash App **tag**.",
        color=discord.Color.orange()
    )
    view = MiddleManButton(channel.id)
    msg = await channel.send(embed=embed, view=view)
    draft["middleman_select_msg_id"] = msg.id
    save_drafts(data)


async def send_payment_instructions(channel, players=None, *, double: bool = False):
    data = load_drafts()
    draft = data.get(str(channel.id))
    if not draft:
        return

    # Delete the Cash App info image once collection is done
    info_id = draft.pop("cashapp_info_msg_id", None)
    if info_id:
        try:
            msg = await channel.fetch_message(info_id)
            await msg.delete()
        except Exception:
            pass
        save_drafts(data)

    if players is None:
        players = draft.get("players", [])

    entry = draft.get("entry_amount", 0)
    middleman_tag = draft.get("middleman_cash_tag", "$unknown")
    total = entry * len(players)

    # Generate a unique 3-letter note for this draft
    note = ''.join(random.choice(string.ascii_uppercase) for _ in range(3))
    draft["payment_note"] = note
    save_drafts(data)

    pending_payments[str(channel.id)] = {
        draft["player_tags"].get(str(uid)): uid
        for uid in players
        if str(uid) in draft.get("player_tags", {})
    }
    payments_received[str(channel.id)] = {uid: 0.0 for uid in players}

    title = "💰 Payment Instructions" if not double else "💰 Double or Nothing Payment"

    embed = discord.Embed(
        title=title,
        description=(
            f"Send your entry fee to the middleman now and include the note `{note}`.\n"
            "Payments without the correct note will not be detected."
        ),
        color=discord.Color.gold()
    )
    embed.add_field(name="📲 Middleman's Cash App Tag", value=middleman_tag, inline=False)
    embed.add_field(name="💵 Amount to Send", value=f"${entry}", inline=False)
    embed.add_field(name="📦 Total Expected", value=f"${total} total from {len(players)} players", inline=False)
    embed.add_field(name="🔒 Required Note", value=f"`{note}`", inline=False)
    embed.set_footer(text="Once all payments are confirmed, the draft will begin.")

    player_mentions = " ".join(f"<@{uid}>" for uid in players)

    # Manual Start Button
    view = PaymentControlView(channel.id, double=double)
    await channel.send(content=player_mentions, embed=embed, view=view)


async def send_pick_options(channel):
    data = load_drafts()
    draft = data.get(str(channel.id))
    if not draft:
        return

    # Clear previous pick buttons
    async for msg in channel.history(limit=10):
        if msg.author == channel.guild.me and msg.components:
            try:
                await msg.delete()
            except:
                pass

    total_picks = len(draft["team1"]) + len(draft["team2"])
    expected_picks = len(draft["players"]) - 2

    if total_picks >= expected_picks:
        await finalize_draft_teams(channel)
        return

    # Picking phase
    pick_turn = draft["pick_turn"]
    pick_captain_id = draft["captains"][pick_turn]
    pick_captain = await channel.guild.fetch_member(pick_captain_id)

    view = discord.ui.View(timeout=None)
    for uid in draft["available"]:
        member = await channel.guild.fetch_member(uid)
        view.add_item(PickButton(member, channel.id))

    await channel.send(f"{pick_captain.mention}, it's your turn to pick:", view=view)

class GoToDraftButton(discord.ui.View):
    def __init__(self, channel):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="🔗 Go to Draft",
            url=channel.jump_url,
            style=discord.ButtonStyle.link
        ))




    
class DraftQueueView(discord.ui.View):
    def __init__(self, channel_id, max_players):
        super().__init__(timeout=None)
        self.channel_id = str(channel_id)
        self.max_players = max_players
        self.status_button = discord.ui.Button(label=f"0/{max_players}", disabled=True, style=discord.ButtonStyle.secondary, custom_id="queue_status")
        self.add_item(self.status_button)

    @discord.ui.button(label="Join Queue", style=discord.ButtonStyle.blurple, custom_id="join_draft")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        scammer_role_id      = 1377442142061858916
        token_player_role_id = 1374569702801670144

        if not any(role.id == token_player_role_id for role in interaction.user.roles):
            await interaction.response.send_message(
                "❌ You need the **Token Player** role to join the draft.\n"
                "Grab it here: <https://discordapp.com/channels/1374569504071221248/1377135618080899094>",
                ephemeral=True
            )
            return

        if any(role.id == scammer_role_id for role in interaction.user.roles):
            await interaction.response.send_message(
                "❌ Scammers can't play drafts. Go pay off your debt!",
                ephemeral=True
            )
            return

        data = load_drafts()
        draft = data.get(self.channel_id)
        if not draft:
            return

        uid = interaction.user.id
        if uid in draft["players"]:
            await interaction.response.send_message("❌ You're already in the queue.", ephemeral=True)
            return

        draft["players"].append(uid)
        save_drafts(data)
        await interaction.channel.set_permissions(interaction.user, send_messages=True)

        await interaction.response.send_message("✅ Joined the queue!", ephemeral=True)
        await self.update_queue_count(interaction.message)
        await update_live_queue(interaction.channel)

        if len(draft["players"]) == self.max_players:
            msg = await interaction.channel.fetch_message(draft["queue_message_id"])
            view = DraftQueueView(self.channel_id, self.max_players)
            disable_all_buttons(view)
            await msg.edit(view=view)
            await auto_start_draft(interaction.guild, interaction.channel)

    @discord.ui.button(label="Leave Queue", style=discord.ButtonStyle.danger, custom_id="leave_draft")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_drafts()
        draft = data.get(self.channel_id)
        if not draft:
            return

        uid = interaction.user.id
        if uid not in draft["players"]:
            await interaction.response.send_message("❌ You're not in the queue.", ephemeral=True)
            return

        draft["players"].remove(uid)
        save_drafts(data)
        await interaction.channel.set_permissions(interaction.user, overwrite=None)

        await interaction.response.send_message("✅ Left the queue.", ephemeral=True)
        await self.update_queue_count(interaction.message)
        await update_live_queue(interaction.channel)

    async def update_queue_count(self, message):
        data = load_drafts()
        count = len(data[self.channel_id]["players"])
        self.status_button.label = f"{count}/{self.max_players}"
        await message.edit(view=self)


class MiddlemanCashTagModal(discord.ui.Modal, title="Enter Your Cash App Tag"):
    def __init__(self, channel_id):
        super().__init__()
        self.channel_id = channel_id

        self.cash_tag = discord.ui.TextInput(
            label="Cash App Tag (include $)",
            placeholder="$example",
            required=True
        )
        self.add_item(self.cash_tag)

    async def on_submit(self, interaction: discord.Interaction):
        submitted_tag = self.cash_tag.value.strip()
        if len(submitted_tag) <= 3:
            await interaction.response.send_message(
                "❌ Cash App tag must be longer than 3 characters.", ephemeral=True
            )
            return
        data = load_drafts()
        draft = data.get(str(self.channel_id), {})
        draft["middleman_cash_tag"] = submitted_tag
        save_drafts(data)

        await interaction.response.send_message(
            f"✅ Your Cash App tag `{submitted_tag}` has been saved.", ephemeral=True
        )

        channel = interaction.guild.get_channel(self.channel_id)
        if channel:
            # Disable the middleman selection button now that it's confirmed
            try:
                data = load_drafts()
                draft = data.get(str(self.channel_id), {})
                msg_id = draft.get("middleman_select_msg_id")
                if msg_id:
                    msg = await channel.fetch_message(msg_id)
                    view = MiddleManButton(self.channel_id)
                    disable_all_buttons(view)
                    await msg.edit(content=f"✅ Middleman: {interaction.user.mention}", view=view)
            except Exception as e:
                print(f"Error disabling middleman button: {e}")

            await begin_cashapp_collection(interaction.guild, channel)




@tree.command(name="createdraft", description="Create a new draft")
@app_commands.describe(
    team_size="Choose 3v3 or 4v4",
    is_money_draft="Require Cash App payment?",
    entry_fee="Dollar amount each player must pay",
    snake_draft="Enable snake draft"
)
@app_commands.choices(team_size=[
    app_commands.Choice(name="3v3", value="3v3"),
    app_commands.Choice(name="4v4", value="4v4"),
])
@app_commands.checks.has_any_role("Drafter", "Draft Admin")
@app_commands.default_permissions()
async def createdraft(
    interaction: discord.Interaction,
    team_size: app_commands.Choice[str],
    is_money_draft: bool = False,
    entry_fee: app_commands.Range[int, 0, None] = 0,
    snake_draft: bool = True,
):
    team_count = int(team_size.name[0])
    max_players = team_count * 2
    now_str = datetime.datetime.now().strftime("%H%M")  # For channel name (e.g., 2132 for 9:32 PM)
    now = int(datetime.datetime.now().timestamp())      # For Discord timestamp formatting
    now = int(datetime.datetime.now().timestamp())

    draft_data = {
        "team_size": team_size.value,
        "snake_draft": snake_draft,
        "is_money_draft": is_money_draft,
        "entry_amount": entry_fee,
        "date": now,
        "players": [],
        "team1": [],
        "team2": [],
        "captains": {},
        "voice_channels": {},
        "team_roles": {},
        "available": [],
        "pick_turn": "team1"
    }



    guild = interaction.guild
    category = discord.utils.get(guild.categories, id=DRAFT_CATEGORY_ID)
    channel = await guild.create_text_channel(name=f"draft-{now_str}", category=category)


    # Allow @everyone to view and send messages in the draft channel
    await channel.set_permissions(guild.default_role, view_channel=True, send_messages=False)


# Allow role 1377074606220906686 to send messages
    role1 = interaction.guild.get_role(1377074606220906686)
    if role1:
        await channel.set_permissions(role1, send_messages=True, view_channel=True)

# Allow role 1374569721185173594 to send messages
    role2 = interaction.guild.get_role(1374569721185173594)
    if role2:
        await channel.set_permissions(role2, send_messages=True, view_channel=True)

# NEW: Allow role 1374569702801670144 to view the channel
    visible_role = guild.get_role(1374569702801670144)
    if visible_role:
        await channel.set_permissions(visible_role, view_channel=True)

    

    data = load_drafts()
    data[str(channel.id)] = draft_data
    save_drafts(data)


    embed = discord.Embed(
        title=f"🎯 Fortnite Drafts {team_size.name}",
        description="We're seeking participants for this draft. Click the button below to join. \n https://discord.com/channels/1374569504071221248/1386875846693748806 ",
        color=discord.Color.blurple()
    )
    embed.add_field(name="👥 Team Size:", value=team_size.name, inline=False)
    embed.add_field(name="📅 Date:", value=f"<t:{now}:F>", inline=False)
    embed.add_field(name="🎙️ Host:", value=interaction.user.mention, inline=False)
    embed.add_field(name="🐍 Snake Draft:", value="Yes" if snake_draft else "No", inline=False)
    if is_money_draft and entry_fee > 0:
        embed.add_field(name="💵 Entry Fee", value=f"${entry_fee}", inline=False)
    embed.set_footer(text=f"Queue: 0/{max_players} • Made by blur.exe")

    view = DraftQueueView(channel.id, max_players)
    await interaction.response.send_message(f"✅ Draft channel created: {channel.mention}", ephemeral=True)
    queue_message = await channel.send("<@&1374569702801670144>", embed=embed, view=view)
    data[str(channel.id)]["queue_message_id"] = queue_message.id
    save_drafts(data)

    # Send second embed for live queue tracking
    live_embed = discord.Embed(
        title="📋 Current Players in Queue",
        description="No players yet.",
        color=discord.Color.blue()
)
    live_message = await channel.send(embed=live_embed)

# Save live embed message ID to drafts.json
    data[str(channel.id)]["live_queue_message_id"] = live_message.id
    save_drafts(data)

    # Auto-delete channel if no players join within 10 minutes
    async def delete_if_empty():
        await asyncio.sleep(600)  # 600 seconds = 10 minutes
        data = load_drafts()
        draft = data.get(str(channel.id))

        if draft and len(draft["players"]) == 0:
            try:
                await channel.send("⏳ No one joined the draft in time. Closing channel...")
                await channel.delete()
                del data[str(channel.id)]
                save_drafts(data)
            except Exception as e:
                print(f"Error auto-deleting draft channel: {e}")

    bot.loop.create_task(delete_if_empty())




@tree.command(name="forcestart", description="Force-start draft if queue is ready")
@app_commands.checks.has_any_role("Draft Admin", "Drafter")
@app_commands.default_permissions()
async def forcestart(interaction: discord.Interaction):
    data  = load_drafts()
    draft = data.get(str(interaction.channel.id))
    if not draft:
        await interaction.response.send_message("❌ No draft found in this channel.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)  # ✅ Prevent interaction timeout

    player_count = len(draft["players"])

    if player_count < 4:
        await interaction.followup.send("❌ You need at least 4 players to start.", ephemeral=True)
        return

    if player_count % 2 != 0:
        await interaction.followup.send(
            f"❌ Cannot force-start with an **odd number** of players ({player_count}). "
            "Wait for one more player or remove one.",
            ephemeral=True
        )
        return

    await auto_start_draft(interaction.guild, interaction.channel)
    await interaction.followup.send("✅ Draft force started.", ephemeral=True)


@tree.command(name="closedraft", description="Close draft and delete channel")
@app_commands.checks.has_any_role("Draft Admin")
@app_commands.default_permissions()
async def closedraft(interaction: discord.Interaction):
    await interaction.response.defer()

    data = load_drafts()
    cid = str(interaction.channel.id)

    if cid not in data:
        await interaction.followup.send("❌ No active draft in this channel.", ephemeral=True)
        return

    draft = data[cid]
    guild = interaction.guild  # ✅ FIX: Define guild properly



    # Delete any legacy VC keys
    for key in ("vc1_id", "vc2_id"):
        vc_id = draft.get(key)
        if vc_id:
            vc = guild.get_channel(vc_id)
            if vc:
                try:
                    await vc.delete()
                except Exception as e:
                    print(f"Error deleting legacy VC {vc.name}: {e}")

# Move members to holding VC and delete VCs first
    await move_all_in_voice_channels(guild, draft.get("voice_channels", {}), 1377124001519898634)

# Delete voice channels
    for vc_id in draft.get("voice_channels", {}).values():
        vc = guild.get_channel(vc_id)
        if vc:
            try:
                await vc.delete()
            except Exception as e:
                print(f"Failed to delete VC {vc_id}: {e}")

# Then clean up data and delete draft text channel
# ✅ Delete team roles if they exist
    for rid in draft.get("team_roles", {}).values():
        role = guild.get_role(rid)
        if role:
            try:
                await role.delete()
            except Exception as e:
                print(f"Error deleting role {rid}: {e}")
    del data[cid]
    save_drafts(data)
    await interaction.channel.delete()

	

@tree.command(name="enddraft", description="End draft and log results")
@app_commands.describe(winning_team="Who won?")
@app_commands.choices(winning_team=[
    app_commands.Choice(name="Team 1", value="team1"),
    app_commands.Choice(name="Team 2", value="team2"),
    app_commands.Choice(name="N/A", value="na"),
])
@app_commands.checks.has_any_role("Draft Admin")
@app_commands.default_permissions() 
async def enddraft(interaction: discord.Interaction, winning_team: app_commands.Choice[str]):
    data = load_drafts()
    cid = str(interaction.channel.id)
    draft = data.get(cid)
    if not draft:
        await interaction.response.send_message("❌ No active draft found.", ephemeral=True)
        return

    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        await interaction.response.send_message("❌ Log channel not found.", ephemeral=True)
        return

    guild = interaction.guild
    team1_ids = draft.get("team1", [])
    team2_ids = draft.get("team2", [])
    c1_id = draft["captains"]["team1"]
    c2_id = draft["captains"]["team2"]

    c1 = await guild.fetch_member(c1_id) if c1_id else None
    c2 = await guild.fetch_member(c2_id) if c2_id else None

    m1 = []
    for uid in team1_ids:
        member = await guild.fetch_member(uid)
        m1.append(member)

    m2 = []
    for uid in team2_ids:
        member = await guild.fetch_member(uid)
        m2.append(member)

    draft["team_size"] = f"{len(draft['team1']) + 1}v{len(draft['team2']) + 1}"


    embed = discord.Embed(title="📊 Draft Results", description=f"**Fortnite Drafts {draft['team_size']}**", color=discord.Color.blue())
    embed.add_field(name="Team Size", value=draft["team_size"], inline=True)
    embed.add_field(name="📅 Date", value=f"<t:{draft['date']}:F>", inline=True)

    if winning_team.value == "team1":
        embed.add_field(name="🏆 Winning Team", value=f"**Captain:** {c1.mention}\n**Members:**\n" + "\n".join(m.mention for m in m1), inline=True)
        embed.add_field(name="📍 Losing Team", value=f"**Captain:** {c2.mention}\n**Members:**\n" + "\n".join(m.mention for m in m2), inline=True)
    elif winning_team.value == "team2":
        embed.add_field(name="🏆 Winning Team", value=f"**Captain:** {c2.mention}\n**Members:**\n" + "\n".join(m.mention for m in m2), inline=True)
        embed.add_field(name="📍 Losing Team", value=f"**Captain:** {c1.mention}\n**Members:**\n" + "\n".join(m.mention for m in m1), inline=True)

    else:
        embed.add_field(name="⚠️ No Winner", value="This draft ended without a declared winner, but here were the teams at the time:", inline=False)
        embed.add_field(
            name="🟦 Team 1",
            value=f"**Captain:** {c1.mention if c1 else 'N/A'}\n" + ("\n".join(m.mention for m in m1) if m1 else "No members."),
            inline=True
    )
        embed.add_field(
            name="🟥 Team 2",
            value=f"**Captain:** {c2.mention if c2 else 'N/A'}\n" + ("\n".join(m.mention for m in m2) if m2 else "No members."),
            inline=True
    )

    embed.set_footer(text="Made by blur.exe")
    await log_channel.send(embed=embed)
    await interaction.response.send_message("✅ Result posted.", ephemeral=True)

    await move_all_in_voice_channels(guild, draft.get("voice_channels", {}), 1377124001519898634)
# Delete voice channels if they exist
    vc_info = draft.get("voice_channels", {})
    for vc_id in vc_info.values():
        vc = interaction.guild.get_channel(vc_id)
        if vc:
            try:
                await vc.delete()
            except Exception as e:
                print(f"Failed to delete voice channel {vc_id}: {e}")

    save_drafts(data)


# Move all users to the holding voice channel
    holding_channel = guild.get_channel(1377124001519898634)  # Your holding VC

    for uid in draft["team1"] + draft["team2"] + [draft["captains"]["team1"], draft["captains"]["team2"]]:
        member = guild.get_member(uid)
        if member and member.voice:
            try:
                await member.move_to(holding_channel)
            except:
                pass  # Fail silently if user can't be moved

# Now delete team voice channels
    voice_ids = draft.get("voice_channels", {})
    vc1 = guild.get_channel(voice_ids.get("team1"))
    vc2 = guild.get_channel(voice_ids.get("team2"))

    if vc1:
        await vc1.delete()
    if vc2:
        await vc2.delete()

# Finally delete the draft channel
# Delete created voice channels
    vc1 = interaction.guild.get_channel(draft.get("vc1_id", 0))
    vc2 = interaction.guild.get_channel(draft.get("vc2_id", 0))
    if vc1:
        await vc1.delete()
    if vc2:
        await vc2.delete()

# Auto-delete voice channels if they exist
    vc1_id = draft.get("vc1_id")
    vc2_id = draft.get("vc2_id")
    if vc1_id:
        vc1 = guild.get_channel(vc1_id)
        if vc1:
            await vc1.delete()
    if vc2_id:
        vc2 = guild.get_channel(vc2_id)
        if vc2:
            await vc2.delete()

    await move_and_delete_voice_channels(interaction.guild, draft)
    # ✅ Delete team roles if they exist
    for rid in draft.get("team_roles", {}).values():
        role = guild.get_role(rid)
        if role:
            try:
                await role.delete()
            except Exception as e:
                print(f"Error deleting role {rid}: {e}")
    del data[cid]
    save_drafts(data)
    await interaction.channel.delete()


async def auto_start_draft(guild, channel, skip_middleman: bool = False):
    data = load_drafts()
    draft = data[str(channel.id)]

    ids = draft["players"]
    captains = random.sample(ids, 2)
    players_per_team = (len(ids) - 2) // 2

    draft["team_size"] = f"{players_per_team}v{players_per_team}"
    draft["captains"]["team1"] = captains[0]
    draft["captains"]["team2"] = captains[1]
    draft["available"] = [uid for uid in ids if uid not in captains]
    draft["team1"] = []
    draft["team2"] = []
    draft["pick_turn"] = "team1"
    save_drafts(data)

    # Disable queue join/leave buttons now that the draft is starting
    await disable_queue_buttons(channel)

    c1 = await guild.fetch_member(captains[0])
    c2 = await guild.fetch_member(captains[1])
    snake = draft["snake_draft"]

    if draft.get("is_money_draft") and not skip_middleman:
        await send_middleman_selection(channel)
        return

    await send_actual_draft_start(channel)
    await send_pick_options(channel)
    await dm_players_draft_started(channel)




async def dm_players_draft_started(channel):
    """Send every queued player a DM that the draft room is open and waiting on the Middle Man."""
    data  = load_drafts()
    draft = data[str(channel.id)]

    dm_embed = discord.Embed(
        title="🎯 Draft Room Created!",
        description=f"A draft room has opened in {channel.mention}.\n"
                    "A Middle Man still needs to confirm payments before the draft begins.",
        color=discord.Color.blue()
    )
    dm_embed.set_footer(text="Made by blur.exe")
    view = GoToDraftButton(channel)

    for uid in draft["players"]:
        try:
            member = await channel.guild.fetch_member(uid)
            await member.send(embed=dm_embed, view=view)
        except Exception as e:
            print(f"❌ Couldn't DM {uid}: {e}")

async def send_actual_draft_start(channel):
    data = load_drafts()
    draft = data[str(channel.id)]
    c1 = await channel.guild.fetch_member(draft["captains"]["team1"])
    c2 = await channel.guild.fetch_member(draft["captains"]["team2"])
    snake = draft["snake_draft"]

    embed = discord.Embed(
        title="🏁 Draft Started!",
        description="The draft has begun. Captains and pick order are listed below.",
        color=discord.Color.green()
    )
    embed.add_field(name="🟦 Team 1 Captain", value=f"{c1.mention} (picks first)", inline=False)
    embed.add_field(name="🟥 Team 2 Captain", value=f"{c2.mention}", inline=False)
    embed.add_field(name="Pick Order", value="🐍 Snake Draft Enabled" if snake else "➡️ Normal Draft Order", inline=False)
    embed.set_footer(text="Made by blur.exe")

    await channel.send(embed=embed)

    # ✅ DM players with an embed and a "Go to Draft" button
    dm_embed = discord.Embed(
        title="🎯 Draft Started!",
        description=f"Your draft has started in {channel.mention}. Click the button below to jump in!",
        color=discord.Color.blue()
    )
    dm_embed.set_footer(text="Made by blur.exe")
    view = GoToDraftButton(channel)

    for uid in draft["players"]:
        try:
            member = await channel.guild.fetch_member(uid)
            await member.send(embed=dm_embed, view=view)
        except Exception as e:
            print(f"❌ Couldn't DM {uid}: {e}")


async def finalize_draft_teams(channel):
    data = load_drafts()
    draft = data.get(str(channel.id))
    if not draft:
        return

    c1 = await channel.guild.fetch_member(draft["captains"]["team1"])
    c2 = await channel.guild.fetch_member(draft["captains"]["team2"])
    m1 = [await channel.guild.fetch_member(uid) for uid in draft["team1"]]
    m2 = [await channel.guild.fetch_member(uid) for uid in draft["team2"]]

    # ✅ Dynamically update team size based on players
    players_per_team = len(draft["team1"])  # excludes captain
    draft["team_size"] = f"{players_per_team + 1}v{players_per_team + 1}"  # +1 for captain

    # ✅ Final Teams Embed
    embed = discord.Embed(
        title="🎯 Final Teams",
        description="Teams have been finalized!",
        color=discord.Color.green()
    )
    embed.add_field(name="🟦 Team 1", value=f"**Captain:** {c1.mention}\n" + "\n".join(m.mention for m in m1), inline=True)
    embed.add_field(name="🟥 Team 2", value=f"**Captain:** {c2.mention}\n" + "\n".join(m.mention for m in m2), inline=True)
    embed.set_footer(text="Made by blur.exe")
    await channel.send(embed=embed)

    # ✅ Create Team Roles
    permissions = discord.Permissions()
    permissions.update(connect=True, view_channel=True, move_members=True)
    channel_number = channel.name.split("-")[-1]  # e.g., "1955"
    team1_role = await channel.guild.create_role(name=f"Team 1 ({draft['team_size']} #{channel_number})", permissions=permissions)
    team2_role = await channel.guild.create_role(name=f"Team 2 ({draft['team_size']} #{channel_number})", permissions=permissions)



# ✅ Assign Roles to Players
    for uid in draft["team1"] + [draft["captains"]["team1"]]:
        member = await channel.guild.fetch_member(uid)
        await member.add_roles(team1_role)

    for uid in draft["team2"] + [draft["captains"]["team2"]]:
        member = await channel.guild.fetch_member(uid)
        await member.add_roles(team2_role)

# ✅ Create Voice Channels With Role Permissions
    voice_category = channel.guild.get_channel(1377123860108804177)
    everyone_role  = channel.guild.default_role

    overwrites_team1 = {
        everyone_role: discord.PermissionOverwrite(view_channel=True, connect=False),
        team1_role:    discord.PermissionOverwrite(view_channel=True, connect=True)
}

    overwrites_team2 = {
        everyone_role: discord.PermissionOverwrite(view_channel=True, connect=False),
        team2_role:    discord.PermissionOverwrite(view_channel=True, connect=True)
}

    team1_vc = await channel.guild.create_voice_channel(
        f"Team 1 ({draft['team_size']})",
        category=voice_category,
        overwrites=overwrites_team1
)

    team2_vc = await channel.guild.create_voice_channel(
        f"Team 2 ({draft['team_size']})",
        category=voice_category,
        overwrites=overwrites_team2
)

    await team1_vc.edit(user_limit=len(draft["team1"]) + 1)
    await team2_vc.edit(user_limit=len(draft["team2"]) + 1)

# ✅ Save VC and Role IDs
    draft["voice_channels"] = {
        "team1": team1_vc.id,
        "team2": team2_vc.id
}
    draft["team_roles"] = {
        "team1": team1_role.id,
        "team2": team2_role.id
}
    save_drafts(data)


    # ✅ Move Members
    async def move_members(member_ids, vc):
        for uid in member_ids:
            member = channel.guild.get_member(uid)
            if member and member.voice:
                try:
                    await member.move_to(vc)
                except Exception as e:
                    print(f"Error moving {member.display_name}: {e}")

    await move_members(draft["team1"] + [draft["captains"]["team1"]], team1_vc)
    await move_members(draft["team2"] + [draft["captains"]["team2"]], team2_vc)

    if draft.get("is_money_draft"):
        await send_double_prompt(channel)

async def move_all_in_voice_channels(guild, vc_ids, target_channel_id):
    target_channel = guild.get_channel(target_channel_id)
    if not target_channel:
        print("❌ Holding VC not found")
        return

    for vc_id in vc_ids.values():
        vc = guild.get_channel(vc_id)
        if vc:
            for member in vc.members:
                try:
                    await member.move_to(target_channel)
                except Exception as e:
                    print(f"Error moving {member.display_name}: {e}")


import imaplib
import email
from email.header import decode_header
import time
import threading


def check_cashapp_emails():
    while True:
        try:
            # Connect to Gmail
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            mail.select("inbox")

            # Search for recent Cash App emails
            result, data = mail.search(None, '(UNSEEN FROM "cash@square.com")')
            if result == "OK":
                for num in data[0].split():
                    result, msg_data = mail.fetch(num, "(RFC822)")
                    if result == "OK":
                        msg = email.message_from_bytes(msg_data[0][1])
                        subject_raw = msg["Subject"]
                        if subject_raw is None:
                            continue  # Skip this email, no subject

                        subject_header = msg.get("Subject")
                        if subject_header is None:
                            continue  # Skip if there's no subject

                        raw_subject = msg.get("Subject")
                        if raw_subject is None:
                            continue  # skip if subject is missing

                        subject, encoding = decode_header(raw_subject)[0]
                        if isinstance(subject, bytes):
                            subject = subject.decode(encoding if encoding else "utf-8")

                        if isinstance(subject, bytes):
                            subject = subject.decode(encoding if encoding else "utf-8")

                        print(f"📩 New email: {subject}")

                        drafts_snapshot = load_drafts()

                        def norm(txt: str) -> str:
                            return re.sub(r"[^a-z0-9]", "", txt.lower())

                        sender_match = re.search(r"^(.*?) sent you", subject, re.IGNORECASE)
                        sender = sender_match.group(1).strip() if sender_match else ""
                        sender_norm = norm(sender)

                        note_match = re.search(r"for\s+(\w{3})", subject, re.IGNORECASE)
                        subject_note = note_match.group(1) if note_match else ""
                        subject_note_norm = norm(subject_note)
                        amt_match = re.search(r"\$(\d+(?:\.\d{1,2})?)", subject)
                        amount_val = float(amt_match.group(1)) if amt_match else 0.0

                        for channel_id, tag_map in pending_payments.items():
                            note = drafts_snapshot.get(str(channel_id), {}).get("payment_note", "")
                            note_norm = norm(note)

                            if note_norm and note_norm != subject_note_norm:
                                continue

                            for tag, user_id in tag_map.items():
                                tag_norm = norm(tag)

                                if sender_norm == tag_norm:
                                    payments = payments_received.setdefault(channel_id, {})
                                    new_total = payments.get(user_id, 0.0) + amount_val
                                    payments[user_id] = new_total

                                    channel = bot.get_channel(int(channel_id))
                                    if channel:
                                        awaitable = channel.send(
                                            f"💸 <@{user_id}> sent ${amount_val:.2f}! Total: ${new_total:.2f}"
                                        )
                                        asyncio.run_coroutine_threadsafe(awaitable, bot.loop)

                                    entry_amt = drafts_snapshot.get(str(channel_id), {}).get("entry_amount", 0)
                                    all_paid = all(
                                        amt >= entry_amt
                                        for uid, amt in payments.items()
                                        if uid in tag_map.values()
                                    )

                                    if all_paid and channel:
                                        current = drafts_snapshot.get(str(channel_id), {})
                                        double_state = current.get("double", {}).get("state")
                                        if double_state == "collecting":
                                            awaitable = channel.send("@everyone Double payment received! Play again!")
                                            data = load_drafts()
                                            cur = data.get(str(channel_id), {})
                                            if "double" in cur:
                                                cur["double"]["state"] = "complete"
                                            save_drafts(data)
                                        else:
                                            awaitable = auto_start_draft(channel.guild, channel, skip_middleman=True)
                                        asyncio.run_coroutine_threadsafe(awaitable, bot.loop)

            mail.logout()

        except Exception as e:
            print(f"[EMAIL ERROR] {e}")

        time.sleep(15)

# Start thread
threading.Thread(target=check_cashapp_emails, daemon=True).start()



@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Logged in as {bot.user.name}")

bot.run("MTM3NzA3MjE4Njk2MzAwNTQ0MA.GCPWMK.pSCzixxkoDWig9VoGq4pVkZTyZYF0oCoSJ1mRQ")