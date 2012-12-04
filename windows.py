#!/usr/bin/env python

import acme
import notmuch
import math
import threading
import email
import email.utils
import codecs
import subprocess

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
			return self.win.handle(ev)

	def __init__(self, id=None):
		super(ThreadedWindow, self).__init__(id=id)
		self.loop = ThreadedWindow.EventLoop(self)

	def handle(self, ev):
		if (ev.type == 'BUTTON_2_TO_TAG' or ev.type == 'BUTTON_2_TO_BODY') and hasattr(self, ev.text):
			return getattr(self, ev.text)(ev)

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
			# Look for the first number in the line on which the user just clicked
			addr1, addr2 = ev.addr1, ev.addr2
			self.addr = "#%d-/^/+/[0-9]+/" % ev.addr1
			nr = int(self.xdata)
			msgs = self.threads[nr-1].get_toplevel_messages()

			# Mark the line (i.e., set dot) on which we just clicked
			self.addr = "#%d-/^/+/.*/" % ev.addr1
			self.set_dot_to_addr()

			# Open a new window
			t = Thread(msgs)
			t.run()
			return True
		else:
			return super(ThreadList, self).handle(ev)

class Thread(ThreadedWindow):
	"""Window for displaying a single thread."""
	def __init__(self, toplevel_messages):
		super(Thread, self).__init__()
		# Keep track of what we've got from above - we may need to keep it around
		self._keeping_track_for_notmuch = toplevel_messages
		# Get a copy of the toplevel messages (we need to iterate over them several times)
		self.toplevel_messages = []
		for msg in toplevel_messages:
			self.toplevel_messages.append(msg)
		self._set_message_hierarchy()
		self.only_matched = True
		# Redraw whole screen - will allso fill self.message_list
		self.Redraw()

	def _set_message_hierarchy(self):
		"""Generator for message hierarchy.

		Produces tuples (msg, depth), where message is a message and
		depth is the message's depth in the message hierarchy, i.e., if a message
		has depth i then all its children have depth i+1.

		Messages are generated in a depth-first traversal of the thread."""
		stack = [ (msg, 0) for msg in self.toplevel_messages ]
		self.message_hierarchy = []
		stack.reverse()
		while stack:
			msg, depth = stack.pop()
			self.message_hierarchy.append((msg, depth))
			replies = [ (reply, depth+1) for reply in msg.get_replies() ]
			replies.reverse()
			stack.extend(replies)

	def _message_hierarchy(self):
		"""Generator for message hierarchy.

		Produces tuples (msg, depth), where message is a message and
		depth is the message's depth in the message hierarchy, i.e., if a message
		has depth i then all its children have depth i+1.

		Messages are generated in a depth-first traversal of the thread."""
		return iter(self.message_hierarchy)

	def ToggleMatch(self, ev):
		"""Toggle showing only messages matching the query. Redraws if called"""
		self.only_matched = not self.only_matched
		self.Redraw(ev)
		return True

	def Redraw(self, ev=None):
		"""Redraw whole screen"""
		data = self.datafile('a')
		last_subject = None
		# Message counter
		i = 0
		# Clear screen
		self.addr = ","
		self.message_list = []
		for message, depth in self._message_hierarchy():
			if self.only_matched and not message.get_flag(notmuch.Message.FLAG.MATCH):
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
			# Find the first number in the line clicked on
			self.addr = "#%d-/^/+/[0-9]+/" % ev.addr1
			nr = int(self.xdata)
			msg = self.message_list[nr - 1]

			# Mark the line (i.e., set dot) on which we just clicked
			self.addr = "#%d-/^/+/.*/" % ev.addr1
			self.set_dot_to_addr()

			# Open new window
			new_win = Message(msg)
			new_win.run()
			return True
		else:
			return super(Thread, self).handle(ev)


class Message(ThreadedWindow):
	"""Window for displaying a single message.

	This window displays a message from a file in Maildir style."""
	HEADERS_TO_SHOW = ['to', 'from', 'subject', 'date', 'cc', 'bcc', 'message-id']
	"List of headers to show, in that order, for the message."
	def __init__(self, message):
		super(Message, self).__init__()
		self.filename = message.get_filename()
		self.tag = "| Reply"
		self.Redraw()

	def Redraw(self):
		# Read message from disk
		with file(self.filename) as f:
			message = email.message_from_file(f)

		# Mark everything so that we replace the window content
		self.addr = ","

		# Many writes to the data file, so we open it
		with self.datafile('a') as data:
			# Print headers
			for key in Message.HEADERS_TO_SHOW:
				if key in message:
					print >> data, key + ": " + message[key]

			# Print separator
			print >> data, "-" * 80

			# Recur over message bodies (arranged like a tree, i.e., messages are either leaves
			# (type is not multipart) or they contain lists of submmessages)
			# Returns true if we displayed something for that message (useful for multipart/alternative)
			def rec(message):
				# Was this node handled, i.e., did we print anything? (Useful for multipart/alternative)
				handled = False
				if message.get_content_maintype() == 'text':
					print >> data, message.get_payload(decode=True)
					handled = True
				elif message.get_content_type() == 'multipart/alternative':
					# Only handle one message
					sub_messages = iter(message.get_payload())
					try:
						while not handled:
							message = next(sub_messages)
							handled = rec(message)
					except StopIteration:
						pass
				elif message.get_content_type() == 'multipart/mixed':
					# Display all messages in a multipart mixed message
					for msg in message.get_payload():
						handled |= rec(msg)
				else:
					# Just print the message's type and, if it has, its filename
					filename = message.get('Content-Disposition') or ""
					print >> data, "[ %s %s ]" % (message.get_content_type(), filename)
					handled = True
				return handled
			rec(message)

		# Done writing - Clean the window and go to top
		self.clean()
		self.addr = "0"
		self.set_dot_to_addr()
		self.show()
	
	def Reply(self, ev):
		reply = self.setup_reply()
		win = NewMessage(**reply)
		win.run()
		return True
	
	def setup_reply_to(self, message):
		# TODO: Handle CC and multiple senders
		senders = message['from']
		return senders
	
	def setup_reply_title(self, message):
		subject = message['subject']
		subject = subject.lower()
		subject = subject.strip()
		if not (subject.startswith("re:") or subject.startswith("re :") or subject.startswith("aw:")):
			subject = "Re: " + subject
			return subject
		return subject
	
	def setup_reply_body(self, message):
		"""Returns the body of a reply from the message displayed by this window."""
		# We will iterate over this message to find the first text message
		def rec(message):
			result = None
			if message.get_content_type() == 'text/plain':
				result = message.get_payload(decode=True)
			elif message.get_content_maintype() == 'multipart':
				try:
					sub_rec = (rec(sub) for sub in message.get_payload())
					result = next(res for res in sub_rec if res)
				except StopIteration:
					pass
			elif message.get_content_type() == 'text/html':
				# TODO: Wash HTML?
				result = message.get_payload(decode=True)
			return result
		body = rec(message)
		if body:
			lines = body.split('\n')
			body = '\n'.join('> ' + line for line in lines)
		return body
	
	def setup_reply(self):
		"""Setups up a dictionary suitable to giving to NewMessage"""
		with file(self.filename) as f:
			message = email.message_from_file(f)
		d = {}
		d['body'] = self.setup_reply_body(message)
		d['subject'] = self.setup_reply_title(message)
		d['to'] = self.setup_reply_to(message)
		if 'message-id' in message:
			# TODO: Add other messages from the same thread as well?
			d['references'] = message['message-id']
		# TODO Do something more sensible about this
		d['sender'] = 'christian@mvonessen.de'
		return d

class NewMessage(ThreadedWindow):
	"""Window for composing new messages.

	So far, this is just a normal text window that reacts to the 'Send' command by
	handing of its contents to msmtp"""
	def __init__(self, body="", sender="", to="", subject="", date=None, **kwargs):
		super(NewMessage, self).__init__()
		if date is None:
			date = email.utils.formatdate()
		kwargs.update({'from': sender, 'to': to, 'subject': subject, 'date': date})
		for k,v in kwargs.items():
			self.data = k + ": " + v + "\n"
		self.data = "\n"
		self.data = body
		self.addr = '0'
		self.set_dot_to_addr()
		self.show()

		self.tag = "Send"

	def Send(self, ev):
		p = subprocess.Popen(["msmtp", "-t"], stdin=self.bodyfile('r'), stdout=self.errorsfile('a'), stderr=subprocess.STDOUT)
		p.wait()
		if p.returncode == 0:
			self.clean()
		return True

def test(query):
	db = notmuch.Database()
	q = notmuch.Query(db, query)
	win = ThreadList(q.search_threads())
	win.run()
	return win

if __name__ == '__main__':
	import sys
	test(" ".join(sys.argv[1:]))
	# w = NewMessage(sender="christian@mvonessen.de", to='/dev/null', subject='Blubb')
	# w.run()