from pyairtable import Api
import configparser
import logging
import sys 

config = configparser.ConfigParser()
config.read('config.ini')
if "--no-log" in sys.argv:
	logging.basicConfig(level=logging.INFO)
else:
	logging.basicConfig(level=logging.INFO,filename=config['DEFAULT']['LogFile'])

at = Api(config['DEFAULT']["secret"])

projects = []
staff = []
events = []

def get_projects(base,id,view):
	new_list = []
	projTbl = at.table(base,id)
	for row in projTbl.all(view=view):
		new_list.append(row['fields']['Project'])
	print(new_list)

def get_staff(base,id,view):
	new_list = {}
	staffTbl = at.table(base,id)
	for row in staffTbl.all(view=view):
		new_list[row['fields']['Tag']] = row['id']
	print(new_list)

def get_events(base,id,view):
	new_list = []
	eventTbl = at.table(base,id)
	for row in eventTbl.all(view=view):
		new_list.append(row['fields']['Name-Short'])
	print(new_list)

def search_related_vols(base,id,view,tag,event):
	volTbl = at.table(base,id)

	match = None

	for row in volTbl.all(view=view):
		if row['fields']['Tag'] == tag and event in row['fields']['Event']:
			match = row
	
	if match:
		print(match['fields'])
	else:
		print("No Match.")
		exit()

	related_by_app = []		
	related_by_event = []
	
	# abbrevs = ["TO","ST","SO","RE","TR","MO"]
	for row in volTbl.all(view=view):
		# check related applicants
		if event in row['fields']['Event'] and row['fields']['Tag'] != match['fields']['Tag']:
			if 'References' in row['fields']:
				if match['fields']['Tag'].lower() in row['fields']['References'].lower():
					print("Found by applicant: %s" % row['fields']['Tag'])
					related_by_app.append(row)
					continue
			# check games
			if 'Region-Encoded' in match['fields'] and 'Region-Encoded' in row['fields'] and 'Desired Games' in match['fields'] and 'Desired Games' in row['fields']:
				for g in match['fields']['Desired Games']:
					if g in row['fields']['Desired Games']:
						for r in match['fields']['Region-Encoded']:
							if r in row['fields']['Region-Encoded']:
								print("Found by event: %s" % row['fields']['Tag'])
								related_by_event.append(row)
			
	print(related_by_app)
	print(related_by_event)

search_related_vols(config['DEFAULT']["volBase"],config['DEFAULT']["volTable"],config['DEFAULT']["volView"])
#get_projects(config['DEFAULT']["projBase"],config['DEFAULT']["projTable"],config['DEFAULT']["projView"])
#get_staff(config['DEFAULT']["staffBase"],config['DEFAULT']["staffTable"],config['DEFAULT']["staffView"])
#get_events(config['DEFAULT']["eventBase"],config['DEFAULT']["eventTable"],config['DEFAULT']["eventView"])