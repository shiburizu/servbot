import asyncio
import configparser
import logging
import disnake
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
from disnake.ext import commands
from disnake.ext.tasks import loop
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

intents = disnake.Intents.default()
intents.members = True
intents.guild_messages = True
intents.message_content = True

bot = commands.Bot(command_prefix='vg!', intents=intents, activity = disnake.Game("beep beep."),test_guilds=[274693418926735361],command_sync_flags = commands.CommandSyncFlags.all())

tweets_cache = []

message_cache = []

projects_list = ['Unloaded']
staff_list = ['Unloaded']
events_list = ['Unloaded']
tasks_list = ['Unloaded']
staff_ids = {}
staff_discords = {}

if os.path.isfile('tweets_cache.json'):
	with open('tweets_cache.json') as file:
		tweets_cache = json.load(file)

if os.path.isfile('message_cache.json'):
	with open('message_cache.json') as file:
		message_cache = json.load(file)

async def task_priority_emoji(t):
	ref = {
			"Tabled" : "💤",
			"Low" : "🟩",
			"Medium" : "🟨",
			"High" : "🟥",
			"Urgent" : "🚨",
			"N/A" : "🔹"
		}
	if 'Task Priority' in t['fields']:
		if t['fields']['Task Priority'] in ref:
			return ref[t['fields']['Task Priority']]
		else:
			return ref["N/A"]
	else:
		return ref["N/A"]
	
async def proj_priority_emoji(t):
	ref = {
		"Tabled" : "💤",
		"Low" : "🔵",
		"Medium" : "🟡",
		"High" : "🔴",
		"Urgent" : "🚨",
		"N/A" : "🔹"
	}
	if 'Project Priority' in t['fields']:
		if t['fields']['Project Priority'] in ref:
			return ref[t['fields']['Project Priority']]
		else:
			return ref["N/A"]
	else:
		return ref["N/A"]

@bot.event
async def on_ready():
	logging.info('Logged in as')
	logging.info(bot.user.name)
	logging.info(bot.user.id)
	logging.info('------')

	if "--no-socials" not in sys.argv:
		await login_twitter()
		await login_bsky()
		do_sync.start()
	if "--no-todo" not in sys.argv:
		update_project_loop.start()
		
	refresh_slash_data.start()
	
@commands.has_permissions(manage_messages=True)
@bot.slash_command(description="Refreshes the to-do list in #956p-todo")
async def refreshtodo(ctx):
	await ctx.response.send_message("To-do list refreshing!")
	await update_projects()

async def project_hint(ctx,string: str):
	string = string.lower()
	res = [p for p in projects_list if string in p.lower()]
	if len(res) > 25:
		res = res[0:24]
	return res

async def staff_hint(ctx,string: str):
	string = string.lower()
	res = [p for p in staff_list if string in p.lower()]
	if len(res) > 25:
		res = res[0:24]
	return res

async def event_hint(ctx,string: str):
	string = string.lower()
	res =  [p for p in events_list if string in p.lower()]
	if len(res) > 25:
		res = res[0:24]
	return res

async def task_hint(ctx,string: str):
	string = string.lower()
	res = [p for p in tasks_list if string in p.lower()]
	if len(res) > 25:
		res = res[0:24]
	return res

@commands.has_permissions(manage_messages=True)
@bot.slash_command(description="Create a new task item or adds a comment to an existing one.")
async def task(ctx, task: str = commands.Param(description='Task name.',autocomplete=task_hint), 
			   comment: str = commands.Param(description='Text to describe the new task or update via comment'),
			   project: str = commands.Param(default=None,name='project',description='Parent project of task. Should already exist in Airtable. Overwrites project for existing task.',autocomplete=project_hint),
			   whomst: str = commands.Param(default=None,name='who',description='956P Staff assigned to task. Comma-separated list accepted. Overwrites assignees for existing task.',autocomplete=staff_hint), 
			   status: str = commands.Param(default=None,description='Current task status. Overwrites status for existing task.',choices=['Todo','In Progress','Ongoing','Done','Waiting','Clarification Needed','Dropped']),
			   priority: str = commands.Param(default=None,description='Task priority. Overwrites priority for existing task.',choices=['Low','Medium','High','Urgent','Tabled']),
			   due: str = commands.Param(default=None,description='Due date in MM/DD/YYYY format. Overwrites due date for existing task.'),
			   attachment: disnake.Attachment = commands.Param(default='')):
	whomst_ids = []
	ignored = []
	if whomst:
		if "," in whomst:
			whomst = whomst.split(",")
			for w in whomst:
				if w in staff_list:
					whomst_ids.append(staff_list[w])
				else:
					ignored.append(w)
		else:
			if whomst != '':
				if whomst in staff_list:
					whomst_ids = [staff_list[whomst]]
				else:
					ignored.append(whomst)
	extra = ""
	if ignored != []:
		extra += "\nIgnored unknown assignees: %s" % ",".join(ignored)
	project_id = []
	if project in projects_list:
		project_id = [projects_list[project]]
	attachment_dict = []
	if attachment != '':
		attachment_dict = [{
			"url" : attachment.url,
			"filename" : attachment.url.split('/')[-1].split('?')[0]
		}]
	if task in tasks_list:
		if any([status,priority,due,project]):
			taskTbl = at.table(config['DEFAULT']['taskBase'],config['DEFAULT']['taskTable'])
			data = {}
			if status:
				data['Status'] = status
			if priority:
				data['Task Priority'] = priority
			if due:
				data['Due Date'] = due	
			if project_id != []:
				data['Project'] = project_id
			taskTbl.update(tasks_list[task],data)
		task_id = [tasks_list[task]]
		author_id = []
		if str(ctx.author.id) in staff_discords:
			author_id = [staff_discords[str(ctx.author.id)]]
		commentTbl = at.table(config['DEFAULT']['commentBase'],config['DEFAULT']['commentTable'])
		new_comment = commentTbl.create({'Comment':comment,'Task':task_id,'Author':author_id,'Attachments':attachment_dict})
		await ctx.response.send_message("Added comment to [%s](%s)\n`%s`" % (new_comment['fields']['Comment-Topic'],new_comment['fields']['Task-URL'][0],comment),suppress_embeds=True)
	else:
		if priority == None:
			priority = 'Todo'
		taskTbl = at.table(config['DEFAULT']['taskBase'],config['DEFAULT']['taskTable'])
		new_task = taskTbl.create({'Task': task, 'Project': project_id, 'Description': comment, 'Assignees': whomst_ids, 'Due Date': due, 'Status': status, 'Task Priority': priority, 'Attachments': attachment_dict},typecast=True)
		await ctx.response.send_message("Created Task: [%s](%s)%s" % (new_task['fields']['Task'],new_task['fields']['Interface URL'],extra),suppress_embeds=True)
		await refresh_slash_data()

@commands.has_permissions(manage_messages=True)
@bot.slash_command(description="Creates a new project or adds a comment to an existing one.")
async def project(ctx, project: str = commands.Param(description='Project name. Posts a comment if already exists.',autocomplete=project_hint),
					comment: str = commands.Param(description='Text to describe the new project or update via comment'),
					event: str = commands.Param(default=None,description='Parent event of project. Overwrites event for existing project.',autocomplete=event_hint),
					status: str = commands.Param(default=None,description='Current Project status.',choices=['Todo','In Progress','Done','Waiting','Clarification Needed','Dropped']),
					priority: str = commands.Param(default=None,description='Current Project priority.',choices=['Low','Medium','High','Urgent','Tabled']),
					due: str = commands.Param(default=None,description='Current Project due date in MM/DD/YYYY format.'),
					attachment: disnake.Attachment = commands.Param(default='')):
	event_id = []
	if event in events_list:
		event_id = [events_list[event]]
	attachment_dict = []
	if attachment != '':
		attachment_dict = [{
			"url" : attachment.url,
			"filename" : attachment.url.split('/')[-1].split('?')[0]
		}]
	project_id = []
	if project in projects_list:
		# project exists, create comment
		if any([status,priority,due,event]):
			projTbl = at.table(config['DEFAULT']['projBase'],config['DEFAULT']['projTable'])
			data = {}
			if status:
				data['Status'] = status
			if priority:
				data['Project Priority'] = priority
			if due:
				data['Due Date'] = due	
			if event_id != []:
				data['Event'] = event_id
			projTbl.update(projects_list[project],data)
		project_id = [projects_list[project]]
		author_id = []
		if str(ctx.author.id) in staff_discords:
			author_id = [staff_discords[str(ctx.author.id)]]
		commentTbl = at.table(config['DEFAULT']['commentBase'],config['DEFAULT']['commentTable'])
		new_comment = commentTbl.create({'Comment':comment,'Project':project_id,'Author':author_id,'Attachments':attachment_dict})
		await ctx.response.send_message("Added comment to [%s](%s)\n`%s`" % (new_comment['fields']['Comment-Topic'],new_comment['fields']['Project-URL'][0],comment),suppress_embeds=True)
	else:
		if priority == None:
			priority = 'Todo'
		# create new project
		projTbl = at.table(config['DEFAULT']['projBase'],config['DEFAULT']['projTable'])
		new_proj = projTbl.create({'Project': project,'Description': comment,'Event': event_id,'Status': status,'Project Priority': priority,'Due Date': due,'Attachments': attachment_dict},typecast=True)
		await ctx.response.send_message("Created Project: [%s](%s)" % (new_proj['fields']['Project'],new_proj['fields']['Interface URL']),suppress_embeds=True)
		await refresh_slash_data()

@commands.has_permissions(manage_messages=True)
@bot.slash_command(description="Create a new timeline item in Airtable.")
async def timeline(ctx, name: str = commands.Param(description='Timeline event name.'),
				type: str = commands.Param(default='',description='Event type.',choices=['Announcement','Start Time','Deadline','Social','Meeting','External','Working','Travel']),
				start: str = commands.Param(default='',name='start',description='Start date in MM/DD/YYYY format.'), 
				end: str = commands.Param(default='',name='end',description='End date in MM/DD/YYYY format.'), 
				comments: str = commands.Param(default='',description='Brief description of item.'),
				event: str = commands.Param(default='',description='Parent event of item.',autocomplete=event_hint),
				whomst: str = commands.Param(default='',name='assignees',description='956P Staff assigned to item. Comma-separated list accepted.',autocomplete=staff_hint)):
	
	whomst_ids = []
	ignored = []
	if "," in whomst:
		whomst = whomst.split(",")
		for w in whomst:
			if w in staff_list:
				whomst_ids.append(staff_list[w])
			else:
				ignored.append(w)
	else:
		if whomst != '':
			if whomst in staff_list:
				whomst_ids = [staff_list[whomst]]
			else:
				ignored.append(whomst)

	event_id = []
	if event in events_list:
		event_id = [events_list[event]]
	
	extra = ""
	if ignored != []:
		extra += "\nIgnored unknown assignees: %s" % ",".join(ignored)

	taskTbl = at.table(config['DEFAULT']['timeBase'],config['DEFAULT']['timeTable'])
	new_task = taskTbl.create({'Project': name,'Type': type,'Comments': comments,'Assignees': whomst_ids,'Date': start,'End Date': end,'Linked Event': event_id},typecast=True)
	await ctx.response.send_message("Created Timeline item: [%s](%s)%s" % (new_task['fields']['Project'],new_task['fields']['Interface URL'],extra),suppress_embeds=True)

@commands.has_permissions(manage_messages=True)
@bot.slash_command(description="Provide a volunteer tag and event, and retrieve a list of related applicants by multiple criteria.")
async def volsearch(ctx, 
					tag: str = commands.Param(description='Volunteer tag.'),
					event_name: str = commands.Param(name='event',description='Event to search.',autocomplete=event_hint)):
	await ctx.response.defer()
	msg = ""
	res_references = await volsearch_by_field(tag,event_name,'References')
	if res_references != []:
		msg += "__Listed as Referral__\n"
		for i in res_references:
			msg += await volunteer_status_string(i,config['DEFAULT']['volBase'],config['DEFAULT']['volTable'],config['DEFAULT']['volView'])
	res_related = await volsearch_by_field(tag,event_name,'Related Applicants',dup=res_references)
	if res_references != []:
		msg += "__Listed as Related__\n"
		for i in res_related:
			msg += await volunteer_status_string(i,config['DEFAULT']['volBase'],config['DEFAULT']['volTable'],config['DEFAULT']['volView'])
	res_tourney = await volsearch_by_tourney(tag,event_name,dup=res_references+res_related)
	if res_tourney != []:
		msg += "__Listed Same Region+Game Choice(s)__\n"
		for i in res_tourney:
			msg += await volunteer_status_string(i,config['DEFAULT']['volBase'],config['DEFAULT']['volTable'],config['DEFAULT']['volView'])
	if msg == "":
		msg = "No related volunteers found for %s in %s." % (tag, event_name)
	else:
		msg = "Found related volunteers for %s in %s:\n" % (tag, event_name) + msg
	await ctx.edit_original_response(msg,suppress_embeds=True)

async def volunteer_status_string(row,base,id,view):
	reviewers = "N/A"
	if 'Reviewer' in row['fields']:
		reviewers = []
		for r in row['fields']['Reviewer']:
			if r in staff_ids:
				reviewers.append(staff_ids[r])
		reviewers = ",".join(reviewers)
	if 'Process' not in row['fields']:
		row['fields']['Process'] = "Todo"
	msg = "- [%s](https://airtable.com/%s/%s/%s/%s) [%s by %s] - *Discord:* `%s`\n" % (
		row['fields']['Tag'],
		base,
		id,
		view,
		row['id'],
		row['fields']['Process'],
		reviewers,
		row['fields']['Discord']
	)
	return msg

async def volsearch_by_field(tag,event_name,field='References',dup=[]):
	if event_name not in events_list:
		return []
	event = events_list[event_name]
	related_by_field = []	
	volTbl = at.table(config['DEFAULT']['volBase'],config['DEFAULT']['volTable'])
	for row in volTbl.all(view=config['DEFAULT']['volView']):

		dup_flag = False
		for d in dup:
			if row['id'] == d['id']:
				dup_flag = True
				break
		if dup_flag == True:
			continue
		
		if 'Event' not in row['fields'] or field not in row['fields']:
			continue
		if event in row['fields']['Event'] and row['fields']['Tag'].lower() != tag.lower():
			if field in row['fields']:
				if tag.lower() in row['fields'][field].lower():
					related_by_field.append(row)
	return related_by_field

async def volsearch_by_tourney(tag,event_name,dup=[]):
	if event_name not in events_list:
		return []
	event = events_list[event_name]
	related_by_event = []
	match = None
	volTbl = at.table(config['DEFAULT']['volBase'],config['DEFAULT']['volTable'])
	for row in volTbl.all(view=config['DEFAULT']['volView']): #find match
		if event in row['fields']['Event'] and row['fields']['Tag'].lower() == tag.lower():
			match = row
			break
	if match: #find same region and desired games
		for row in volTbl.all(view=config['DEFAULT']['volView']):
			
			dup_flag = False
			for d in dup:
				if row['id'] == d['id']:
					dup_flag = True
					break
			if dup_flag == True:
				continue

			if 'Region-Encoded' not in row['fields'] or 'Desired Games' not in row['fields'] or 'Event' not in row['fields']:
				continue
			if event in row['fields']['Event'] and row['fields']['Tag'].lower() != tag.lower():
				for game in match['fields']['Desired Games']:
					if game in row['fields']['Desired Games']:
						for region in match['fields']['Region-Encoded']:
							if region in match['fields']['Region-Encoded']:
								related_by_event.append(row)
	return related_by_event
	
@loop(minutes=10,reconnect=True)
async def update_project_loop():
	await update_projects()

@loop(minutes=5,reconnect=True)
async def do_sync():
	await share_posts()
	await list_tweets()

async def get_all_linked_rows(base,id,view,key):
	new_list = {}
	targetTbl = at.table(base,id)
	for row in targetTbl.all(view=view):
		new_list[row['fields'][key]] = row['id']
	return new_list

@loop(minutes=5,reconnect=True)
async def refresh_slash_data():
	global projects_list,staff_list,events_list, tasks_list
	projects_list = await get_all_linked_rows(
		config['DEFAULT']["projBase"],
		config['DEFAULT']["projTable"],
		config['DEFAULT']["allProjView"],
		'Project')
	tasks_list = await get_all_linked_rows(
		config['DEFAULT']["taskBase"],
		config['DEFAULT']["taskTable"],
		config['DEFAULT']["allTaskView"],
		'Task')
	staff_list = await get_all_linked_rows(
		config['DEFAULT']["staffBase"],
		config['DEFAULT']["staffTable"],
		config['DEFAULT']["staffView"],
		'Tag')
	events_list = await get_all_linked_rows(
		config['DEFAULT']["eventBase"],
		config['DEFAULT']["eventTable"],
		config['DEFAULT']["eventView"],
		'Name-Short')
	# refresh IDs of staff for lookup
	staffTbl = at.table(config['DEFAULT']["staffBase"],config['DEFAULT']["staffTable"])
	for row in staffTbl.all(view=config['DEFAULT']["staffView"]):
		staff_ids[row['id']] = row['fields']['Tag']
		staff_discords[row['fields']['Discord ID']] = row['id']

@commands.has_permissions(manage_messages=True)
@bot.slash_command(description="Run it up!")
async def runitup(ctx):
	if ctx.channel.id == int(config['DEFAULT']['TechChannel']):
		resp = requests.get(url=config['DEFAULT']['RunItWebhook'])
		if resp.status_code == 200:
			await ctx.response.send_message("We gon run it up")
		else:
			await ctx.response.send_message("Wrong status code: %s. Double check make?" % resp.status_code)

@commands.is_owner()
@bot.slash_command()
async def off(ctx):
	await ctx.response.send_message("Bye bye!")
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

async def generate_task_string(task):
	mentions = []
	if 'Assignees-Discord' in task['fields']:
		for a in task['fields']['Assignees-Discord']:
			mentions.append("<@%s>" % a)
	mentions = " ".join(mentions).strip()
	if 'Shortlink' not in task['fields']:
		url = await generate_link(task['fields']['Interface URL'])
		task['fields']['Shortlink'] = url
		taskTbl = at.table(config['DEFAULT']['taskBase'],config['DEFAULT']['taskTable'])
		taskTbl.update(task['id'],{'Shortlink':url})
	task_string = "\n- `%s` **[[%s]](%s)** %s %s" % (
		await task_priority_emoji(task),
		task['fields']['Status'],
		task['fields']['Shortlink'],
		task['fields']['Task'],
		mentions
	)
	return task_string.rstrip()

async def generate_project_string(project):
	if 'Shortlink' not in project['fields']:
		url = await generate_link(project['fields']['Interface URL'])
		project['fields']['Shortlink'] = url
		projTbl = at.table(config['DEFAULT']['projBase'],config['DEFAULT']['projTable'])
		projTbl.update(project['id'],{'Shortlink':url})
	project_string = "\n### `%s` [[%s]](%s) %s" % (
		await proj_priority_emoji(project),
		project['fields']['Status'],
		project['fields']['Shortlink'],
		project['fields']['Project']
	)
	return project_string

async def update_projects():
	projTbl = at.table(config['DEFAULT']['projBase'],config['DEFAULT']['projTable'])
	taskTbl = at.table(config['DEFAULT']['taskBase'],config['DEFAULT']['taskTable'])
	strings = []
	cur_event = None

	for proj in projTbl.all(view=config['DEFAULT']['projView']):
		if 'Event-Rollup' in proj['fields']:
			if cur_event != proj['fields']['Event-Rollup']:
				strings.append("\n# ```%s```\n" % proj['fields']['Event-Rollup'][0])
				cur_event = proj['fields']['Event-Rollup']
			strings.append(await generate_project_string(proj))
			for task in taskTbl.all(view=config['DEFAULT']['taskView']):
				if proj['id'] in task['fields']['Project']:
					strings.append(await generate_task_string(task))
	#orphaned proj
	orphan_projects = False
	for proj in projTbl.all(view=config['DEFAULT']['projOrphanView']):
		if orphan_projects == False:
			strings.append("\n# ```Projects - No Event```\n")
			orphan_projects = True
		strings.append(await generate_project_string(proj))
		for task in taskTbl.all(view=config['DEFAULT']['taskView']):
			if proj['id'] in task['fields']['Project']:
				strings.append(await generate_task_string(task))
	#orphaned tasks
	orphan_tasks = False
	for task in taskTbl.all(view=config['DEFAULT']['taskOrphanView']):
		if orphan_tasks == False:
			strings.append("\n# ```Tasks - No Project```\n")
			orphan_tasks = True
		strings.append(await generate_task_string(task))
	await combine_messages(strings)

async def old_update_projects():
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

		
@commands.has_permissions(manage_messages=True)
@bot.slash_command(description="Sends you a DM with your currently assigned tasks in Airtable.")
async def todo(ctx):
	await ctx.response.defer()
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
				await dm.send(m,suppress_embeds=True)
			await ctx.edit_original_response("Task list sent via DM!")
		else:
			for m in msg:
				await ctx.edit_original_response(m,suppress_embeds=True)
	else:
		await ctx.edit_original_response("No tasks are assigned to you right now. Enjoy your day!")


@commands.has_permissions(manage_messages=True)
@bot.slash_command(description="Forces a rebuild of the site based on latest Airtable data. Can only run every 10 minutes.")
async def updatesite(ctx):
	global lastSiteRebuild
	now = time.time()
	if lastSiteRebuild != 0:
		diff = now - lastSiteRebuild
		if diff >= 600:
			await ctx.response.send_message("Website update requested.")
			subprocess.run('cd /home/editor/vortexgallery.moe && git pull && python3 update_site.py >> ../site_update.log 2>&1 && git add -A && git commit -m "automated update" && git push',shell=True)
			lastSiteRebuild = now
		else:
			await ctx.send("Please wait at least 10 minutes before sending a site update request. Last build was: <t:%s:R>" % int(lastSiteRebuild)) 
	else:
		await ctx.response.send_message("Website update requested.")
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
	except twErrors.UserNotFound:
		logging.warning("Got UserNotFound error, skipping.")
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
				await share_twitter_posts(m) #get Twitter posts and RT
				await share_bsky_posts(m) #get Bsky posts and RT
				await share_masto_posts(m) #get masto posts and RT
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
				if i[0] != '956.productions':
					try:
						post = BskyClient.get_post(i[1],i[0])
						BskyClient.repost(uri=post.uri,cid=post.cid)
						logging.info('Reposted Bsky post ID %s' % i[1])
						await message.add_reaction("🔁")
						message_cache.append(message.id)
					except disnake.errors.HTTPException:
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
					except disnake.errors.HTTPException:
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

bot.run(BOT_TOKEN,reconnect=True)
