import asyncio
import configparser
import logging
import discord
import re
import os
import json
import requests
import subprocess
import time
import sys
from twikit import Client as twClient
from twikit import errors as twErrors
from atproto import Client as atClient
from mastodon import Mastodon as feClient
from discord.ext import commands
from discord.ext.tasks import loop
from pyairtable import Api

TwitterClient = twClient('en-US',user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TwitterRegex = r"(?:x|twitter)\.com\/([^\/]+)\/status\/([^\/?\s]+)"

lastSiteRebuild = 0

BskyClient = atClient(base_url='https://bsky.social')
BskyRegex = r"bsky\.app\/profile\/([^\/?]+)\/post\/([^\/?\s]+)"

MastoRegex = r"(https:\/\/[^\.]+\.[^\.\s|\n]+)"

## READ CONFIG FILE
config = configparser.ConfigParser()
config.read('config.ini')
if "--no-log" in sys.argv:
	logging.basicConfig(level=logging.INFO)
else:
	logging.basicConfig(level=logging.INFO,filename=config['DEFAULT']['LogFile'])
BOT_TOKEN = config['DEFAULT']['BotToken']

at = Api(config['DEFAULT']["secret"])

MastoClient = feClient(access_token=config['DEFAULT']['MastoToken'],api_base_url=config['DEFAULT']['MastoInstance'])

intents = discord.Intents.default()
intents.members = True
intents.guild_messages = True
intents.message_content = True

bot = commands.Bot(command_prefix='vg!', intents=intents, activity = discord.Game("beep beep."))

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
	if "--no-socials" not in sys.argv:
		do_sync.start()
	update_project_loop.start()
	
@commands.has_permissions(manage_messages=True)
@bot.command()
async def projects(ctx):
	await ctx.reply("Projects refreshing...",mention_author=False,delete_after=5)
	await update_projects()

@loop(minutes=10,reconnect=True)
async def update_project_loop():
	await update_projects()

@loop(minutes=5,reconnect=True)
async def do_sync():
	await share_posts()
	await list_tweets()

@commands.has_permissions(manage_messages=True)
@bot.command()
async def runitup(ctx):
	if ctx.channel.id == int(config['DEFAULT']['TechChannel']):
		resp = requests.get(url=config['DEFAULT']['RunItWebhook'])
		if resp.status_code == 200:
			await ctx.send("We gon run it up")
		else:
			await ctx.send("Wrong status code: %s. Double check make?" % resp.status_code)

@commands.has_permissions(manage_messages=True)
@bot.command()
async def shutdown(ctx):
	await ctx.bot.close()
	exit()

async def combine_messages(msgs):
	msglst = [""]
	msgs.append("\n### Quick Links: ✅ [To-do List](https://956pro.com/todo)  📋 [Projects](https://956pro.com/projects)  📅 [Timeline](https://956pro.com/timeline)")
	for m in msgs:
		if len(msglst[-1]) + len(m) < 2000:
			msglst[-1] += m
		else:
			msglst.append(m)
	msglst.insert(0,"# 956P Project Tracking\nUpdate projects and tasks on this list through Airtable:\n- [To-do List](https://956pro.com/todo)\n- [Project List](https://956pro.com/projects)\n- [Events Timeline](https://956pro.com/timeline)\n-# Last Updated <t:%s:R>, refreshes every 10 mins. Ping Shib if problems." % int(time.time()))
	ch = bot.get_channel(int(config['DEFAULT']['projCh']))
	if ch != None:
		stale_messages = [message async for message in ch.history(limit=10,oldest_first=True)]
		compare_list = []
		update_needed = False
		for m in stale_messages:
			if m.content != "List updated!" and m.content != "-# Reserved for to-do list.":
				compare_list.append(m.content)
		if compare_list != []:
			counter = 0
			for i in compare_list:
				try:
					if counter != 0: #skip the first message, that's our header with the updating timestamp
						if msglst[counter].strip() != i:
							update_needed = True
					counter += 1
				except IndexError:
					update_needed = True
					break
		else:
			update_needed = True
		if update_needed == True:
			for m in stale_messages:
				await m.edit(content="-# Reserved for to-do list.")
			edit_messages = [message async for message in ch.history(limit=10,oldest_first=True)]
			for m in msglst:
				if m != '':
					try:
						await edit_messages[msglst.index(m)].edit(content=m,suppress=True)
					except IndexError:
						await ch.send(content=m,suppress_embeds=True)
			if update_needed == True:
				await ch.send("List updated!",delete_after=10)

async def generate_link(url):
	req = requests.get("http://l.shib.live/link?key=%s&dest=%s" % (config['DEFAULT']['linkAPI'],url))
	if req.status_code == 200:
		return str(req.content.decode(encoding='utf-8'))
	else:
		return url

async def generate_task_string(task,project_id):
	if 'Task' not in task:
		return None
	if 'Project' not in task:
		task['Project'] = []
	if 'Event Name-Rollup' not in task or task['Event Name-Rollup'] == []:
		task['Event Name-Rollup'] = "No Event Assigned"
	elif task['Event Name-Rollup'] != []:
		task['Event Name-Rollup'] = task['Event Name-Rollup'][0]
	if project_id in task['Project'] or task['Project'] == []:
		if 'Task Priority' not in task:
			task['Task Priority'] = "N/A"
		task_string = ""
		if task['Task Priority'] == 'Tabled':
			task_string += "`💤` "
		elif task['Task Priority'] == 'Low':
			task_string += "`🟦` "
		elif task['Task Priority'] == 'Medium':
			task_string += "`🟨` "
		elif task['Task Priority'] == 'High':
			task_string += "`🟥` "
		elif task['Task Priority'] == 'Urgent':
			task_string += "`🚨` "
		elif task['Task Priority'] == 'N/A':
			task_string += "`🔹` "
		if 'Status' in task:
			task_string += "[**[%s]**](%s) " % (task['Status'],task['Shortlink'])
		else:
			task_string += "[**[??]**](%s) " % task['Status']
		task_string += task['Task']
		if 'Assignees-Discord' in task:
			for d in task['Assignees-Discord']:
				task_string+= " <@%s>" % d
		if 'Due Date-Timestamp' in task:
			task_string += " `⏰ due` <t:%s:R>" % task['Due Date-Timestamp']
		if task['Dependencies-Count'] != 0:
			task_string += " `📝 %s deps`" % task['Dependencies-Count']
		if task['Timeline-Count'] != 0:
			task_string += " `📆 %s`" % task['Timeline-Count']
		if task['Attachments-Count'] != 0:
			task_string += " `📁 %s`" % task['Attachments-Count']
		if task['Comments-Count'] != 0:
			task_string += " `💬 %s`" % task['Comments-Count']
		task_string += "\n"
		return task_string
	return None

async def generate_project_string(project):
	if 'Project' not in project:
		return None
	project_string = "## "
	if 'Project Priority' not in project:
		project['Project Priority'] = 'N/A'
	if project['Project Priority'] == 'Tabled':
		project_string += "💤` "
	elif project['Project Priority'] == 'Low':
		project_string += "`🔵` "
	elif project['Project Priority'] == 'Medium':
		project_string += "`🟡` "
	elif project['Project Priority'] == 'High':
		project_string += "`🔴` "
	elif project['Project Priority'] == 'Urgent':
		project_string += "`🚨` "
	elif project['Project Priority'] == 'N/A':
		project_string += "`🔹` "
	if 'Status' in project:
		project_string += "[[%s]](%s) " % (project['Status'],project['Shortlink'])
	else:
		project_string += "[[??]](%s) " % (project['Shortlink'])
	project_string += project['Project']
	project_string += "\n"
	return project_string

@commands.has_permissions(manage_messages=True)
@bot.command()
async def newtask(ctx,*,arg):
	taskTbl = at.table(config['DEFAULT']['taskBase'],config['DEFAULT']['taskTable'])
	new_task = taskTbl.create({'Task': arg})
	await ctx.reply("Created task: [%s](%s)" % (new_task['fields']['Task'],new_task['fields']['Interface URL']),suppress_embeds=True)

@commands.has_permissions(manage_messages=True)
@bot.command()
async def newevent(ctx,*,arg):
	taskTbl = at.table(config['DEFAULT']['timeBase'],config['DEFAULT']['timeTable'])
	new_task = taskTbl.create({'Project': arg})
	await ctx.reply("Created timeline event: [%s](%s)" % (new_task['fields']['Project'],new_task['fields']['Interface URL']),suppress_embeds=True)

@commands.has_permissions(manage_messages=True)
@bot.command()
async def newproj(ctx,*,arg):
	projTbl = at.table(config['DEFAULT']['projBase'],config['DEFAULT']['projTable'])
	new_proj = projTbl.create({'Project': arg})
	await ctx.reply("Created task: [%s](%s)" % (new_proj['fields']['Project'],new_proj['fields']['Interface URL']),suppress_embeds=True)

async def update_projects():
	projTbl = at.table(config['DEFAULT']['projBase'],config['DEFAULT']['projTable'])
	taskTbl = at.table(config['DEFAULT']['taskBase'],config['DEFAULT']['taskTable'])
	events = {}
	orphaned_tasks = []

	for projrow in projTbl.all(view=config['DEFAULT']['projView']):
		info = projrow['fields']

		if 'Shortlink' not in info:
			newlink = await generate_link(info['Interface URL'])
			projTbl.update(projrow['id'],{'Shortlink':newlink})
			info['Shortlink'] = newlink

		if type(info['Event Name-Rollup']) == list:
			if len(info['Event Name-Rollup']) != 0:
				info['Event Name-Rollup'] = info['Event Name-Rollup'][0]
			else: 
				info['Event Name-Rollup'] = "No Event Assigned"
		else:
			info['Event Name-Rollup'] = "No Event Assigned"

		project_string = await generate_project_string(info)
		
		if info['Event Name-Rollup'] in events:
			events[info['Event Name-Rollup']].append(project_string)
		else:
			events[info['Event Name-Rollup']] = [ project_string ]

		#get each task for the project, add them to the events message list
		for taskrow in taskTbl.all(view=config['DEFAULT']['taskView']):
			task = taskrow['fields']

			if 'Shortlink' not in task:
				newlink = await generate_link(task['Interface URL'])
				taskTbl.update(taskrow['id'],{'Shortlink':newlink})
				task['Shortlink'] = newlink

			task_string = await generate_task_string(task,projrow['id'])
			if task_string != None:
				if info['Event Name-Rollup'] == task['Event Name-Rollup']:
					if projrow['id'] in task['Project']:
						events[info['Event Name-Rollup']].append(task_string)
				else:
					if task_string not in orphaned_tasks:
						orphaned_tasks.append(task_string)
	if orphaned_tasks != []:
		orphaned_tasks.append("## `❔` No Project Assigned\n")
		if 'No Event Assigned' in events:
			events['No Event Assigned'] += (orphaned_tasks)
		else:
			events['No Event Assigned'] = orphaned_tasks
	all_lists = []
	for e in events:
		events[e].insert(0,"## __*%s*__\n" % e)
		all_lists += events[e]
	await combine_messages(all_lists)

		
@bot.command()
@commands.has_permissions(manage_messages=True)
async def todo(ctx):
	table = at.table(config['DEFAULT']['taskBase'],config['DEFAULT']['taskTable'])
	taskdict = {}
	for row in table.all(view=config['DEFAULT']['taskView']):
		if 'Assignees-Discord' in row['fields']:
			if str(ctx.author.id) in row['fields']['Assignees-Discord']:
				info = row['fields']
				if 'Project Priority' not in info:
					info['Project Priority'] = "N/A"
				elif type(info['Project Priority']) == list:
					info['Project Priority'] = info['Project Priority'][0]
				if 'Task Priority' not in info:
					info['Task Priority'] = "N/A"
				task_string = ""
				if info['Task Priority'] == 'Tabled':
					task_string += "`💤` "
				elif info['Task Priority'] == 'Low':
					task_string += "`🟩` "
				elif info['Task Priority'] == 'Medium':
					task_string += "`🟨` "
				elif info['Task Priority'] == 'High':
					task_string += "`🟥` "
				elif info['Task Priority'] == 'Urgent':
					task_string += "`🚨` "
				elif info['Task Priority'] == 'N/A':
					task_string += "`❔` "
				if 'Status' in info:
					task_string += "**[%s]** " % info['Status']
				task_string += info['Task']
				if 'Event-Rollup' in info:
					task_string += " **(" + ",".join(info['Event-Rollup']) + ")**"
				task_string += " [(open)](" + info['Interface URL'] + ")"
				if 'Due Date-Timestamp' in info:
					task_string += " `⏰ due` <t:%s:R>" % info['Due Date-Timestamp']
				if info['Dependencies-Count'] != 0:
					task_string += " `📝 %s deps`" % info['Dependencies-Count']
				if info['Timeline-Count'] != 0:
					task_string += " `📆 %s`" % info['Timeline-Count']
				if info['Attachments-Count'] != 0:
					task_string += " `📁 %s`" % info['Attachments-Count']
				if info['Comments-Count'] != 0:
					task_string += " `💬 %s`" % info['Comments-Count']
				task_string += "\n"

				if info['Project Priority'] in taskdict:
					if info['Task Priority'] in taskdict[info['Project Priority']]:
						taskdict[info['Project Priority']][info['Task Priority']] += task_string
					else:
						taskdict[info['Project Priority']][info['Task Priority']] = task_string
				else:
					taskdict[info['Project Priority']] = { info['Task Priority'] : task_string }
	if taskdict != {}:
		msg = ["## 956P Tasks (<t:%s:d>)\n" % int(time.time())]
		for i in taskdict:
			header_msg = "### __**%s Priority Projects**__\n" % i
			if len(msg[-1]) + len(header_msg) >= 2000:
				msg.append(header_msg)
			else:
				msg[-1] += header_msg
			for t in taskdict[i]:
				body_msg = taskdict[i][t]
				if len(msg[-1]) + len(body_msg) >= 2000:
					msg.append(body_msg)
				else:
					msg[-1] += body_msg

		if ctx.guild != None:
			dm = await ctx.author.create_dm()
			for m in msg:
				await dm.send(m,suppress_embeds=True,mention_author=False)
			await ctx.reply("Task list sent via DM!",mention_author=False)
		else:
			for m in msg:
				await ctx.send(m,suppress_embeds=True,mention_author=False)
	else:
		await ctx.reply("No tasks are assigned to you right now. Enjoy your day!",mention_author=False)


@bot.command()
@commands.has_permissions(manage_messages=True)
async def updatesite(ctx):
	global lastSiteRebuild
	now = time.time()
	if lastSiteRebuild != 0:
		diff = now - lastSiteRebuild
		if diff >= 600:
			await ctx.send("Website update requested.")
			subprocess.run('cd /home/editor/vortexgallery.moe && git pull && python3 update_site.py >> ../site_update.log 2>&1 && git add -A && git commit -m "automated update" && git push',shell=True)
			lastSiteRebuild = now
		else:
			await ctx.send("Please wait at least 10 minutes before sending a site update request. Last build was: <t:%s:R>" % int(lastSiteRebuild)) 
	else:
		await ctx.send("Website update requested.")
		subprocess.run('cd /home/editor/vortexgallery.moe && git pull && python3 update_site.py >> ../site_update.log 2>&1 && git add -A && git commit -m "automated update" && git push',shell=True)
		lastSiteRebuild = now

async def list_tweets():
	op = None
	posts = None
	try:
		op = await TwitterClient.get_user_by_screen_name("956productions")
		posts = await TwitterClient.get_user_tweets(op.id,"Tweets")
	except twErrors.AccountSuspended:
		logging.warning("Got AccountSuspended error from Twitter, skipping.")
		return False
	for i in reversed(posts):
		if int(i.id) not in tweets_cache and int(i.id) > int(config['DEFAULT']['StartTweet']) and i.text[:2] != "RT":
			tweets_cache.append(int(i.id))
			await bot.get_channel(int(config['DEFAULT']['StaffChannel'])).send("RTs appreciated! 🔗 https://vxtwitter.com/956productions/status/%s" % i.id)
			await bot.get_channel(int(config['DEFAULT']['PublicChannel'])).send(i.text,suppress_embeds=True)
			await bot.get_channel(int(config['DEFAULT']['PublicChannel'])).send("RTs appreciated! 🔗 https://vxtwitter.com/956productions/status/%s" % i.id,suppress_embeds=False)
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
				#get Twitter posts and RT
				await share_twitter_posts(m)

				#get Bsky posts and RT
				await share_bsky_posts(m)

				#get masto posts and RT
				await share_masto_posts(m)

	with open("message_cache.json","w") as file:
		json.dump(message_cache,file)

async def check_if_retweeted(post):
	try:
		rt = await post.get_retweeters()
		for u in rt:
			if u.screen_name == config['DEFAULT']['TwitterUser']:
				return True
		return False
	except:
		return False

async def share_twitter_posts(message):
	twLinks = re.findall(TwitterRegex,message.content)
	if len(twLinks) > 0:
		for i in twLinks:
			if message.id > int(config['DEFAULT']['StartMessage']):
				try:
					post = await TwitterClient.get_tweet_by_id(i[1])
					shared = await check_if_retweeted(post)
					if shared == True:
						await message.add_reaction("🔁")
						logging.info('Confirmed RT for tweet ID %s' % i[1])
						message_cache.append(message.id)
					else:
						await post.retweet()
						await asyncio.sleep(3)
						result = await check_if_retweeted(post)
						if result == True:
							await message.add_reaction("🔁")
							logging.info('Confirmed RT for tweet ID %s' % i[1])
							message_cache.append(message.id)
				except:
					pass

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
					message_cache.append(message.id)
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
						message_cache.append(message.id)
					except discord.errors.HTTPException:
						pass

async def login_twitter():
	if os.path.isfile(config['DEFAULT']['CookiesFile']):
		TwitterClient.load_cookies(config['DEFAULT']['CookiesFile'])
	else:
		await TwitterClient.login(
			auth_info_1=config['DEFAULT']['TwitterUser'],
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
	if "--no-socials" not in sys.argv:
		await login_twitter()
		await login_bsky()
	async with bot:
		await bot.start(BOT_TOKEN,reconnect=True)

asyncio.run(main())