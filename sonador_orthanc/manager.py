import six, os, json, logging, pprint, threading, requests, traceback, posixpath
from concurrent.futures import ThreadPoolExecutor as ThreadPool

import orthanc

from client.errors import ConfigurationError

from sonador.servers import SonadorServer

TIMER_30S = 30
TIMER_MINUTE = 60
TIMER_10MIN = TIMER_MINUTE*10
TIMER_30MIN = TIMER_MINUTE*30
TIMER_HOUR = TIMER_MINUTE*60
TIMER_DAILY = TIMER_HOUR*24

logger = logging.getLogger(__name__)


class SonadorServerManager:
	'''	Manages the integration between Sonador and Orthanc and provides methods for
		scheduling recurring tasks, executing long-running operations, and invoking callbacks
		on server changes.
	'''
	threads_count = 4

	def __init__(self, sonador_conn: SonadorServer, imageserver_id: str, 
			threadpool=None, timers=None, changeCallbacks=None, **kwargs):
		'''	Initialize server manager
		'''
		self.server = sonador_conn
		self.imageserver_id = imageserver_id
		self.threadpool = threadpool or ThreadPool(
			max_workers=kwargs.get('threads_count', self.threads_count))
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
					self.timers[schedule]['timer'] = threading.Timer(schedule, run_tasks)
					self.timers[schedule]['timer'].start()

			# Schedule initial run of task (does not execute until manager.start called)
			self.timers[schedule]['timer'] = threading.Timer(schedule, run_tasks)

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