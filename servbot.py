import asyncio
import configparser
import logging
import discord
import re
import os
from twikit import Client as twClient
from atproto import Client as atClient
from discord.ext import commands
from discord.ext.tasks import loop

TwitterClient = twClient('en-US')
TwitterRegex = r"(?:x|twitter)\.com\/([^\/]+)\/status\/([^\/?]+)"

BskyClient = atClient(base_url='https://bsky.social')
BskyRegex = r"bsky\.app\/profile\/([^\/?]+)\/post\/([^\/?]+)"

## READ CONFIG FILE
config = configparser.ConfigParser()
config.read('config.ini')

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = config['DEFAULT']['BotToken']

intents = discord.Intents.default()
intents.members = True
intents.guild_messages = True

bot = commands.Bot(command_prefix='&', intents=intents, activity = discord.Game("beep beep."))

@bot.event
async def on_ready():
	logging.info('Logged in as')
	logging.info(bot.user.name)
	logging.info(bot.user.id)
	logging.info('------')
	#share_posts.start()
	await share_posts()
	await bot.close()
	exit()

#@loop(seconds=10,reconnect=True)
async def share_posts():
	messages = [message async for message in bot.get_channel(int(config['DEFAULT']['Channel'])).history(limit=50)]
	for m in messages:
		alreadyShared = False
		for r in m.reactions:
			if str(r) == u"🔁":
				users = [user async for user in r.users()]
				for u in users:
					if u.id == int(config['DEFAULT']['BotID']):
						alreadyShared = True
		if alreadyShared == False:
			#get Twitter posts and RT
			await share_twitter_posts(m)
			#get Bsky posts and RT
			await share_bsky_posts(m)
		

async def share_twitter_posts(message):
	twLinks = re.findall(TwitterRegex,message.content)
	if len(twLinks) > 0:
		for i in twLinks:
			if message.id > int(config['DEFAULT']['StartMessage']):
				resp = await TwitterClient.retweet(i[1])
				logging.info(resp)
				if resp.status_code == 200:
					try:
						await message.add_reaction("🔁")
					except discord.errors.HTTPException:
						pass
					logging.info('Reposted Twitter post ID %s' % i[1])

async def share_bsky_posts(message):
	atLinks = re.findall(BskyRegex,message.content)
	if len(atLinks) > 0:
		for i in atLinks:
			if message.id > int(config['DEFAULT']['StartMessage']):
				try:
					post = BskyClient.get_post(i[1],i[0])
					BskyClient.repost(uri=post.uri,cid=post.cid)
					logging.info('Reposted Bsky post ID %s' % i[1])
					await message.add_reaction("🔁")
				except discord.errors.HTTPException:
					pass

async def login_twitter():
	try:
		TwitterClient.load_cookies(config['DEFAULT']['CookiesFile'])
	except:
		await TwitterClient.login(
			auth_info_1=config['DEFAULT']['TwitterUser'],
			auth_info_2=config['DEFAULT']['TwitterEmail'],
			password=config['DEFAULT']['TwitterPass']
		)
		TwitterClient.save_cookies(config['DEFAULT']['CookiesFile'])

async def login_bsky():
	if os.path.isfile('bsky_session.txt'):
		with open("bsky_session.txt","r") as file:
			bsky_session_string = file.readline().replace("\n","")
			if bsky_session_string == "":
				BskyClient.login(config['DEFAULT']['BskyUser'],config['DEFAULT']['BskyPass'])
			else:
				BskyClient.login(config['DEFAULT']['BskyUser'],config['DEFAULT']['BskyPass'],bsky_session_string)
	else:
		BskyClient.login(config['DEFAULT']['BskyUser'],config['DEFAULT']['BskyPass'])

	with open("bsky_session.txt","w") as file:
		file.write(BskyClient.export_session_string())

async def main():
	await login_twitter()
	await login_bsky()
	async with bot:
		await bot.start(BOT_TOKEN)

asyncio.run(main())
