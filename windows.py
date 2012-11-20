import acme
import notmuch
import math
import threading
import email
import codecs

class ThreadedWindow(acme.Window):
	"""A window that controls its own events in its own thread.

	This class combines the capabilities of an acme.Window (by inheritance)
	and an acme.EventLoop (by composition).
	
	This means that you can use the usual acme.Window methods to modify
	this window, and that you can either override the handle method or
	add methods that should be callable as for an acme.EventLoop.
	
	Finally, if you run() an instance of this class, it will not block but
	spawn a new thread for its eventloop."""
	class EventLoop(acme.EventLoop):
		def __init__(self, win):
			super(ThreadedWindow.EventLoop, self).__init__(win)
			
		def handle(self, ev):
			self.win.handle(ev)
			
	def __init__(self, id=None):
		super(ThreadedWindow, self).__init__(id=id)
		self.loop = ThreadedWindow.EventLoop(self)
	
	def handle(self, ev):
		if (ev.type == 'BUTTON_2_TO_TAG' or ev.type == 'BUTTON_2_TO_BODY') and hasattr(self, ev.text):
			getattr(self, ev.text)(ev)
			return True
		
	def run(self):
		self.t = threading.Thread(target=self.loop.run)
		self.t.start()
		return self.t

class ThreadList(ThreadedWindow):
	"""Window for displaying a thread."""
	def __init__(self, threads):
		"""Initialize new thread list window from iterable of threads."""
		super(ThreadList, self).__init__()
		# Cache list of threads (we will need to iterate over them several times)
		self.threads = []
		for t in threads:
			self.threads.append(t)
		try:
			self.Redraw()
		finally:
			self.clean()
	
	def Redraw(self):
		"""Redraw the contents of this window completely.
		
		Can be called as a window command."""
		# Delete everything
		self.addr = ","
		self.data = ""
		
		# Draw list of threads top to bottom
		
		number_of_threads = len(self.threads)
		# Number of digits necessary to print largest thread number (used for filling)
		number_of_digits =int(math.floor(math.log(number_of_threads, 10)) + 1)
		data = self.datafile('a')
		# Counts thread number
		i = 0
		for thread in self.threads:
			i += 1
			# Terrible format string
			# First the (local) thread number, with configurable width,
			# then the number of messages matching the query in comparison to 
			# the number of messages in the thread,
			# then all author's names, finally the subject (after a semicolon)
			format_string = u"{nr:{width}d}\t [{matched}/{all}] {authors}; {subject}"
			formatted = format_string.format(
				nr=i, width=number_of_digits,
				subject=thread.get_subject() or '[None]',
				authors=thread.get_authors(),
				matched=thread.get_matched_messages(),
				all=thread.get_total_messages()
			)
			print >>data, unicode.encode(formatted, "utf-8")
	
	def handle(self, ev):
		# If we handle a button 3 (search button) event in the body, then we find the current
		# line we are in (by looking for the first number in that line)
		# then we pen a new window with that thread
		# Every other command is handled by the parent
		if ev.type == 'BUTTON_3_TO_BODY' and ev.flag == 0:
			addr1, addr2 = ev.addr1, ev.addr2
			self.addr = "#%d-/^/+/[0-9]+/" % ev.addr1
			nr = int(self.xdata)
			msgs = self.threads[nr-1].get_toplevel_messages()
			t = Thread(msgs)
			t.run()
			return True
		else:
			return super(ThreadList, self).handle(ev)

class Thread(ThreadedWindow):
	"""Window for displaying a single thread."""
	def __init__(self, toplevel_messages):
		super(Thread, self).__init__()
		# Get a copy of the toplevel messages (we need to iterate over them several times)
		self.toplevel_messages = []
		for msg in toplevel_messages:
			self.toplevel_messages.append(msg)
		self.only_matched = True
		# Redraw whole screen - will allso fill self.message_list
		self.Redraw()
	
	def _message_hierarchy(self):
		"""Generator for message hierarchy.
		
		Produces tuples (msg, depth), where message is a message and
		depth is the message's depth in the message hierarchy, i.e., if a message
		has depth i then all its children have depth i+1.
		
		Messages are generated in a depth-first traversal of the thread."""
		stack = [ (msg, 0) for msg in self.toplevel_messages ]
		stack.reverse()
		while stack:
			msg, depth = stack.pop()
			yield (msg, depth)
			replies = [ (reply, depth+1) for reply in msg.get_replies() ]
			replies.reverse()
			stack.extend(replies)
	
	def ToggleMatch(self, ev):
		"""Toggle showing only messages matching the query. Redraws if called"""
		self.only_matched = not self.only_matched
		self.Redraw(ev)
	
	def Redraw(self, ev=None):
		"""Redraw whole screen"""
		data = self.datafile('a')
		last_subject = None
		# Message counter
		i = 0
		self.message_list = []
		for message, depth in self._message_hierarchy():
			if not self.only_matched or not message.get_flag(notmuch.Message.FLAG.MATCH):
				continue
			self.message_list.append(message)
			i = i + 1
			# String to fill line from the left
			shift = "| " * depth
			width = 4 # Number of digits reseverd for message numbers
			if last_subject != message.get_header('Subject'):
				format_line = u"{nr:{width}d} {shift} {author} {subject}"
				last_subject = message.get_header('Subject')
			else:
				format_line = u"{nr:{width}d} {shift} {author}"
			formatted = format_line.format(
				author=message.get_header('From'),
				subject=message.get_header('Subject'),
				shift=shift,
				width=width,
				nr = i)
			print >>data, unicode.encode(formatted, "utf-8")
	
	def handle(self, ev):
		if ev.type == 'BUTTON_3_TO_BODY' and ev.flag == 0:
			self.addr = "#%d-/^/+/[0-9]+/" % ev.addr1
			nr = int(self.xdata)
			msg = self.message_list[nr - 1]
			new_win = Message(msg)
			new_win.run()
			return True
		else:
			return super(Thread, self).handle(ev)
			

class Message(ThreadedWindow):
	HEADERS_TO_SHOW = ['to', 'from', 'subject', 'date', 'cc', 'bcc']
	def __init__(self, message):
		super(Message, self).__init__()
		self.filename = message.get_filename()
		self.Redraw()
	
	def Redraw(self):
		with file(self.filename) as f:
			message = email.message_from_file(f)
		self.addr = ","
		with self.datafile('a') as data:
			for key in Message.HEADERS_TO_SHOW:
				if key in message:
					print >> data, key + ": " + message[key]
			
			print >> data, message.get_charsets()
			
			print >> data, "-" * 80
			
			def rec(message):
				if message.get_content_maintype() == 'text':
					print >> data, message.get_payload(decode=True)
					return True
				elif message.get_content_type() == 'multipart/alternative':
					sub_messages = message.get_payload()
					for message in sub_messages:
						if rec(message):
							break
				elif message.get_content_type() == 'multipart/mixed':
					for msg in message.get_payload():
						rec(msg)
				else:
					print >> data, "[ " + message.get_content_type() + " ]"
			rec(message)
		self.clean()
		self.addr = "0"
		self.set_dot_to_addr()
		self.show()

def test(query):
	db = notmuch.Database()
	q = notmuch.Query(db, query)
	win = ThreadList(q.search_threads())
	win.run()
	return win

if __name__ == '__main__':
	test("from:kimberly tag:attachment")