import six, os, json, logging, pprint, threading, requests, traceback, posixpath
from concurrent.futures import ThreadPoolExecutor as ThreadPool

import orthanc

from client import apisettings as capicodes
from client.errors import ConfigurationError, ResourceDoesNotExist
from client.utils.object import pick

from sonador.servers import SonadorServer, SonadorImagingServer
from sonador.remote import sonador_dataobject_create

TIMER_30S = 30
TIMER_MINUTE = 60
TIMER_10MIN = TIMER_MINUTE*10
TIMER_30MIN = TIMER_MINUTE*30
TIMER_HOUR = TIMER_MINUTE*60
TIMER_DAILY = TIMER_HOUR*24

logger = logging.getLogger(__name__)



IMAGE_SERVER_CONFIG_TRANSFORMS = {
	'OrthancServerScheme': 'scheme',
	'OrthancServerHostname':  'hostname',
	'OrthancServerPort': 'port',
	'OrthancServerName': 'name',
	'OrthancServerDescription':  'description',
	'OrthancServerInternalScheme': 'internal_scheme',
	'OrthancServerInternalHostname': 'internal_hostname',
	'OrthancServerInternalPort': 'internal_port',
	"OrthancServerActive": 'active',
}


class SonadorServerManager:
	'''	Manages the integration between Sonador and Orthanc and provides methods for
		scheduling recurring tasks, executing long-running operations, and invoking callbacks
		on server changes.
	'''
	threads_count = 4
	registration_delay = 30
	retry_limit = 3
	retry_interval = 30

	def __init__(self, sonador_conn: SonadorServer, imageserver_id: str, 
			threadpool=None, timers=None, changeCallbacks=None, **kwargs):
		'''	Initialize server manager
		'''
		self.server = sonador_conn
		self.imageserver_id = imageserver_id
		self.threadpool = threadpool or ThreadPool(
			max_workers=kwargs.get('threads_count', self.threads_count))
		self.retry_limit = kwargs.get('retry_limit', self.retry_limit)
		self.retry_interval = kwargs.get('retry_interval', self.retry_interval)

		# Orthanc/Sonador configuration		
		self.conf = kwargs.get('conf') or {}

		# Manager state
		self.running = False

		# Recurring tasks and change event handlers
		self.timers = timers or {}
		self.changeCallbacks = changeCallbacks or {}

		# Create and global onchange handler for manager
		def server_onchange(ctype, level, resource):
			'''	Execute change callbacks for the specified type
			'''	
			self.trigger_serverchange_callback(ctype, level, resource)

		# Register global onchange handler
		orthanc.RegisterOnChangeCallback(server_onchange)

	def shutdown_orthanc(self, *args, **kwargs):
		'''	Shutdown the Orthanc instance by making a call to '/tools/shutdown'.
		'''
		return orthanc.RestApiPost('/tools/shutdown', '')

	def register_server(self, *args, **kwargs):
		''' Synchronize local server configuration with remote configuration on Sonador. If an entry
			does not exit exist, it will be created.
		'''
		# Retry registration up to limit
		retry = kwargs.get('retry', 0)
		if retry < self.retry_limit:

			try:

				# Transform configuration to database schema
				sdata = { 'uid': self.imageserver_id }
				for ckey in IMAGE_SERVER_CONFIG_TRANSFORMS:
					if self.conf.get(ckey):
						sdata[IMAGE_SERVER_CONFIG_TRANSFORMS.get(ckey)] = self.conf.get(ckey)

				# Retrieve and update image server entry
				try:
					iserver = self.server.get_imageserver(self.imageserver_id)
					iserver = iserver.update(sdata)

				# Create server entry if it does not exist
				except ResourceDoesNotExist as err:
					rdata = sonador_dataobject_create(self.server, SonadorImagingServer, sdata, verify=self.server.verify)

					# Ensure that the Sonador assigned server ID matches the local server ID
					if rdata.get(capicodes.UPDATE_URL):

						# Parse server assigned ID from update URL, retrieve instance to ensure
						# it was created correctly within Sonador database and compare UID to the local UID.
						_, iserver_uid = posixpath.split(rdata.get(capicodes.UPDATE_URL))
						iserver = self.serrver.get_imageserver(iserver_uid)
						assert self.imageserver_id == iserver.pk

				logger.warning('Orthanc instance %s registered with Sonador successfully' % self.imageserver_id)
				return

			# Queue retry 
			except Exception as err:
				logger.critical('Unable to register Orthanc instance %s with Sonador. Retry (%s/%s) in %s seconds'
					% (self.imageserver_id, retry+1, self.retry_limit, self.retry_interval))

				# Retry registration of the server in 30 seconds
				self.create_scheduled_task(30, lambda: self.register_server(retry=retry+1))

		# Unable to register server with Sonador: stop Orthanc
		else:
			logger.critical('Unable to register Orthanc instance %s with Sonador (failed %s/%s attempts).'
				% (self.imageserver_id, retry, self.retry_limit))
			self.shutdown_orthanc()

	def create_scheduled_task(self, interval, task, *args, start=True, **kwargs):
		'''	Creates a timer instance which will execute the provided task in the future.

			@input interval (number): number of seconds to wait before executing the provided task
			@input task (callable): function to be invoked by the timer
			@input task (start, default=True): when True, the method will invoke the "start" method of
				the task instance.

			@returns threading.Timer
		'''
		timer = threading.Timer(interval, task, *args, **kwargs)
		if start:
			timer.start()

		return timer

	def register_recurring_task(self, schedule, task):
		'''	Add a recurring task to the server manager
		'''
		# Ensure that the manager instance is not running
		if self.running:
			raise ConfigurationError('Unable to add recurring task, server manager is currently running.')

		# Check to see if a timer has been created for the specified interval,
		# if not create timer and callback cache. Configure callbacks to run when
		# manager.start is called.
		if not self.timers.get(schedule):
			self.timers[schedule] = { 'timer': None, 'tasks': [] }

			def run_tasks():
				'''	Execute all scheduled tasks
				'''
				logger.info('Server Manager: execute scheduled tasks (schedule=%s): num-tasks=%s' 
					% (schedule, len(self.timers[schedule].get('tasks', []))))

				# Set timer reference to None
				try:
					self.timers[schedule]['timer'] = None

					# Iterate through all tasks in the schedule and execute in the background
					for stask in self.timers[schedule].get('tasks', []):
						
						def background_task():
							'''	 Try to execute background task using thread pool, log any errors
							'''
							try: stask()
							except Exception as err:
								logger.error('Unable to execute recurring task (schedule=%s). Error:\n%s' % err)

						self.threadpool.submit(background_task)

				except Exception as err:
					logger.error('Unable to execute scheduled tasks (schedule=%s). Error:\n%s' % (schedule, err))

				finally:

					# Schedule next run of the task
					self.timers[schedule]['timer'] = self.create_scheduled_task(schedule, run_tasks, start=True)

			# Schedule initial run of task (does not execute until manager.start called)
			self.timers[schedule]['timer'] = self.create_scheduled_task(schedule, run_tasks, start=False)

		# Add task to callbacks chain
		self.timers[schedule]['tasks'].append(task)

	def register_serverchange_callback(self, changeType, changeCallback):
		''' Add a server change callback to the manager
		'''
		# Create callback cache
		if not self.changeCallbacks.get(changeType):
			self.changeCallbacks[changeType] = []

		self.changeCallbacks[changeType].append(changeCallback)

	def trigger_serverchange_callback(self, ctype, level, resource):
		'''	Execute change callbacks for the specified type
		'''
		if self.changeCallbacks.get(ctype):
			for chandler in self.changeCallbacks[ctype]:
				self.threadpool.submit(chandler, ctype, level, resource)

	def start(self, *args, **kwargs):
		'''	Start all timers registered with the manager
		'''
		if self.running:
			raise ConfigurationError('Server manager is already running')

		for schedule, tasks in self.timers.items():
			if tasks.get('timer'):
				logger.info('Server Manager: start scheduler (%s): num-tasks=%s' 
					% (schedule, len(tasks.get('tasks', []))))
				tasks.get('timer').start()

		# Mark server manager as running
		self.running = True

	def stop(self, *args, **kwargs):
		'''	Stop all timers registered with the manager
		'''
		if not self.running:
			raise ConfigurationError('Server manager is not running')

		# Cancel all scheduled tasks in timers, clear schedule from manager
		for schedule in self.timers:
			
			tasks = self.timers.pop(schedule)
			if tasks.get('timer'):
				tasks.get('timer').cancel()

			logger.info(
				'Server Manager: stop scheduler (%s): num-tasks=%s' % schedule, tasks.get('tasks', []))

		# Shutdown thread pool
		self.threadpool.shutdown(wait=True)

		# Mark server manager as stopped
		self.running = False