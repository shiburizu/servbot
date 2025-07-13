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

get_projects(config['DEFAULT']["projBase"],config['DEFAULT']["projTable"],config['DEFAULT']["projView"])

get_staff(config['DEFAULT']["staffBase"],config['DEFAULT']["staffTable"],config['DEFAULT']["staffView"])

get_events(config['DEFAULT']["eventBase"],config['DEFAULT']["eventTable"],config['DEFAULT']["eventView"])