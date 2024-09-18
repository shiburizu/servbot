import asyncio
import configparser
import logging
import discord
import re
import os
import json
from twikit import Client as twClient
from atproto import Client as atClient
from mastodon import Mastodon as feClient
from discord.ext import commands
from discord.ext.tasks import loop

TwitterClient = twClient('en-US',user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0")
TwitterRegex = r"(?:x|twitter)\.com\/([^\/]+)\/status\/([^\/?\s]+)"

BskyClient = atClient(base_url='https://bsky.social')
BskyRegex = r"bsky\.app\/profile\/([^\/?]+)\/post\/([^\/?\s]+)"

MastoRegex = r"(https:\/\/[^\.]+\.[^\.\s|\n]+)"

## READ CONFIG FILE
config = configparser.ConfigParser()
config.read('config.ini')
logging.basicConfig(level=logging.INFO,filename=config['DEFAULT']['LogFile'])
BOT_TOKEN = config['DEFAULT']['BotToken']

MastoClient = feClient(access_token=config['DEFAULT']['MastoToken'],api_base_url=config['DEFAULT']['MastoInstance'])

intents = discord.Intents.default()
intents.members = True
intents.guild_messages = True

bot = commands.Bot(command_prefix='&', intents=intents, activity = discord.Game("beep beep."))

tweets_cache = []

message_cache = []

if os.path.isfile('tweets_cache.json'):
	with open('tweets_cache.json') as file:
		tweets_cache = json.load(file)

if os.path.isfile('message_cache.json'):
	with open('message_cache.json') as file:
		message_cache = json.load(file)

@bot.event
async def on_ready():
	logging.info('Logged in as')
	logging.info(bot.user.name)
	logging.info(bot.user.id)
	logging.info('------')
	do_sync.start()

@loop(minutes=5,reconnect=True)
async def do_sync():
	await share_posts()
	await list_tweets()

async def list_tweets():
	op = await TwitterClient.get_user_by_screen_name("956productions")
	posts = await TwitterClient.get_user_tweets(op.id,"Tweets")
	for i in posts:
		if int(i.id) not in tweets_cache and int(i.id) > int(config['DEFAULT']['StartTweet']) and i.text[:2] != "RT":
			tweets_cache.append(int(i.id))
			await bot.get_channel(int(config['DEFAULT']['StaffChannel'])).send("RTs appreciated! https://vxtwitter.com/956productions/status/%s" % i.id)
	with open("tweets_cache.json","w") as file:
		json.dump(tweets_cache,file)

async def share_posts():
	messages = [message async for message in bot.get_channel(int(config['DEFAULT']['StaffChannel'])).history(limit=50)]

	for m in messages:
		if m.author.id != bot.user.id and m.id not in message_cache:
			alreadyShared = False
			for r in m.reactions:
				if str(r) == u"🔁":
					users = [user async for user in r.users()]
					for u in users:
						if u.id == int(config['DEFAULT']['BotID']):
							alreadyShared = True
			if alreadyShared == False:
				message_cache.append(m.id)
				
				#get Twitter posts and RT
				await share_twitter_posts(m)

				#get Bsky posts and RT
				await share_bsky_posts(m)

				#get masto posts and RT
				await share_masto_posts(m)

	with open("message_cache.json","w") as file:
		json.dump(message_cache,file)

async def share_twitter_posts(message):
	twLinks = re.findall(TwitterRegex,message.content)
	if len(twLinks) > 0:
		for i in twLinks:
			if message.id > int(config['DEFAULT']['StartMessage']):
				post = await TwitterClient.get_tweet_by_id(i[1])
				await post.retweet()
				await message.add_reaction("🔁")
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

async def share_masto_posts(message):
	feLinks = re.findall(MastoRegex,message.content)
	if len(feLinks) > 0:
		for i in feLinks:
			isTweet = re.findall(TwitterRegex,i)
			isBsky = re.findall(BskyRegex,i)
			if isTweet == [] and isBsky == []:
				post = MastoClient.search(i,True,result_type="statuses")
				if len(post['statuses']) != 0:
					MastoClient.status_reblog(post['statuses'][0]['id'])
					#follow the account so we can get their info reliably in the future
					MastoClient.account_follow(post['statuses'][0]['account']['id'])
					try:
						await message.add_reaction("🔁")
						logging.info('Reposted Masto post ID %s' % post['statuses'][0]['id'])
					except discord.errors.HTTPException:
						pass

async def login_twitter():
	if os.path.isfile(config['DEFAULT']['CookiesFile']):
		TwitterClient.load_cookies(config['DEFAULT']['CookiesFile'])
	else:
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
		await bot.start(BOT_TOKEN,reconnect=True)

asyncio.run(main())