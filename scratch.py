import re

with open("events.py", "r", encoding="utf-8") as f:
    content = f.read()

# Replace "def register_events(bot: commands.Bot):" with "class LoggingCog(commands.Cog):\n    def __init__(self, bot):\n        self.bot = bot"
content = re.sub(r'def register_events\(bot: commands\.Bot\):', 'class LoggingCog(commands.Cog):\n    def __init__(self, bot: commands.Bot):\n        self.bot = bot', content)

# Replace "@bot.event" with "@commands.Cog.listener()"
content = content.replace('@bot.event', '@commands.Cog.listener()')

# Add "self, " to all event listeners
content = re.sub(r'@commands\.Cog\.listener\(\)\n\s+async def (on_[a-zA-Z0-9_]+)\(', r'@commands.Cog.listener()\n    async def \1(self, ', content)

# Remove on_ready and on_message completely.
# They are the first two events. We will just use regex to remove them.
# It might be easier to just remove them manually later. Let's do a naive string replacement or just keep them and remove them via multi_replace.

with open("cogs/logging_events.py", "w", encoding="utf-8") as f:
    f.write(content + "\n\nasync def setup(bot: commands.Bot):\n    await bot.add_cog(LoggingCog(bot))\n")
