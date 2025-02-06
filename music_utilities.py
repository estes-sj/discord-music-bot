from collections import namedtuple

class Queue:
    """
    A class used to represent a queue.

    A Queue is a playlist of songs per Session.

    Attributes
    ----------
    current_music_url : str
        The url of the current music the bot is playing.

    current_music_title : str
        The name of the current music the bot is playing.

    current_music_thumb : str
        The thumbnail url of the current music the bot is playing.

    current_music_ytube : str
        The youtube url of the current music the bot is playing.

    current_music_duration : str
        The duration of the current music the bot is playing in seconds.

    current_music_user : str
        The user ID that added the current music to the queue.

    last_title_enqueued : str
        The title of the last music enqueued.

    queue : tuple list
        The actual queue of songs to play.
        (title, url, thumb, ytube, duration)

    Methods
    -------
    enqueue(music_title, music_url, music_thumb, music_ytube, duration, user)
        Enqueue the music tuple to the queue while setting last_title_enqueued
	and the current_music variables as needed

    dequeue()
        Removes the last music enqueued from the queue.

    next()
        Sets the next music in the queue as the current one.

    theres_next()
        Checks if there is a music in the queue after the current one.

    clear_queue()
        Clears the queue, resetting all variables as needed.

    clear_queue_except_current()
        Clears the queue, except the current song.

    is_empty()
        Checks if the queue is empty.

    size()
        Returns the size of the queue.

    """
    def __init__(self):
        self.music = namedtuple('music', ('title', 'url', 'thumb', 'ytube', 'duration', 'user'))
        self.current_music = self.music('', '', '', '', '', '')

        self.last_title_enqueued = ''
        self.queue = []

    def set_last_as_current(self):
        """
        Sets last music as current.

        :return: None
        """
        index = len(self.queue) - 1
        if index >= 0:
            self.current_music = self.queue[index]

    def enqueue(
        self, 
        music_title: str, 
        music_url: str, 
        music_thumb: str, 
        music_ytube: str, 
        music_duration: int, 
        music_user: int
    ) -> None:
        """
        Enqueue the music tuple to the queue while setting last_title_enqueued
	    and the current_music variables as needed

        :param music_title: str
            The music title to be added to queue
        :param music_url: str
            The music url to be added to queue
        :param music_thumb: str
            The music thumbnail url to be added to queue
        :param music_ytube: str
            The original youtube url to be added to queue
        :param music_duration: str
            The music duration in seconds to be added to queue
        :param music_user: str
            The user ID for the song to be added to queue
        :return: None
        """
        if len(self.queue) > 0:
            self.queue.append(self.music(music_title, music_url, music_thumb, music_ytube, music_duration, music_user))
        else:
            self.queue.append(self.music(music_title, music_url, music_thumb, music_ytube, music_duration, music_user))
            self.current_music = self.music(music_title, music_url, music_thumb, music_ytube, music_duration, music_user)

    def dequeue(self):
        """
        Removes the first music enqueued from the queue and updates the current music accordingly.

        :return: None
        """
        if self.queue:
            self.queue.pop(0)  # Removes the first music in the queue
            if self.queue:  # If there are still songs in the queue
                self.current_music = self.queue[0]  # Set the next song as current
            else:
                self.current_music = self.music('', '', '', '', '', '')  # Clear current music if the queue is empty

    def next(self):
        """
        Sets the next music in the queue as the current one.

        :return: None
        """
        if self.current_music in self.queue:
            index = self.queue.index(self.current_music) + 1
            if len(self.queue) - 1 >= index:
                if self.current_music.title == self.queue[index].title and len(self.queue) - 1 > index + 1:
                    self.current_music = self.queue[index + 1]
                else:
                    self.current_music = self.queue[index]

        else:
            self.clear_queue()

    def theres_next(self):
        """
        Checks if there is a music in the queue after the current one.

        :return: bool
            True if there is a next song in queue.
            False if there isn't a next song in queue.
        """
        if self.queue.index(self.current_music) + 1 > len(self.queue) - 1:
            return False
        else:
            return True

    def clear_queue(self):
        """
        Clears the queue, resetting all variables as needed.

        :return: None
        """
        self.queue.clear()
        self.current_music = self.music('', '', '', '', '', '')

    def clear_queue_except_current(self):
        """
        Clears the queue, except the current playing song.

        :return: None
        """
        if len(self.queue) > 1:
            # Save the current song
            current = self.current_music

            # Clear the queue and re-add the playing song
            self.queue.clear()
            self.current_music = current

            self.queue = [current]  # Keep the currently playing song

    def is_empty(self):
        """
        Checks if the queue is empty.

        :return: bool
            True if there is are no items in the queue
            False if there is at least one item in the queue
        """
        return len(self.queue) <= 0

    def size(self):
        """
        Returns the size of the current queue.

        :return: int
            Number of items in the queue
        """
        return len(self.queue)
    
    def get_current_music(self):
        """
        Returns the current music playing from the queue.

        :return: tuple
            The current_music of the queue
        """
        return self.current_music

class Session:
    """
    A class used to represent an instance of the bot.

    To prevent queue conflicts when multiple guilds send commands to the bot,
    each session is uniquely identified by its associated guild ID and voice channel ID.
    This ensures that music requests remain isolated to their respective guilds.

    Attributes
    ----------
    guild : int
        Unique session identifier and guild ID where the bot is connected.
    channel : int
        Voice channel ID where the bot is connected.
    q : Queue
        Queue instance to handle music playback.
    """

    def __init__(self, guild: int, channel: int) -> None:
        """
        Initializes a session with a unique identifier.

        :param guild: int
            Unique session identifier and guild ID where the bot is connected.
        :param channel: int
            Voice channel ID where the bot is connected.
        """
        self.guild: int = guild
        self.channel: int = channel
        self.q: Queue = Queue()