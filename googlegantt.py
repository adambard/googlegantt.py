"""
Create a gantt chart using the Google Charts API. Inspired by
http://www.designinginteractive.com/code/how-to-build-a-gantt-chart-with-the-google-charts-api/

Copyright (c) 2011 Adam Bard (adambard.com)

Licensed under the MIT License: http://www.opensource.org/licenses/mit-license
"""

import datetime
import urllib
import urllib2

GOOGLE_CHARTS_API_URL = 'https://chart.googleapis.com/chart'
DEFAULT_COLOR = '4D89F9FF' # A kind of nice blue.

class InvalidTaskError(Exception):
    pass

class InvalidDateError(Exception):
    pass

def uniquify(seq, idfun=None):
    """
    Order-preserving uniquify of a list.
    Lifted directly from http://www.peterbe.com/plog/uniqifiers-benchmark
    """

    if idfun is None:
        idfun = lambda x: x

    seen = {}
    result = []
    for item in seq:
        marker = idfun(item)

        if marker in seen:
            continue

        seen[marker] = 1

        result.append(item)

    return result

def as_date(d):
    "Try to convert a value to a date, or raise an InvalidDateError if it can't."
    if isinstance(d, datetime.date):
        return d
    if isinstance(d, tuple):
        return datetime.date(*d)
    raise InvalidDateError('%s is not a valid date' % d)

def parse_color(color):
    "Accept various input formats for color and convert to 8-byte hex RGBA"
    if len(color) == 3:
        color = color[0]*2 + color[1]*2 + color[2]*2 + 'FF'
    if len(color) == 4:
        color = color[0]*2 + color[1]*2 + color[2]*2 + color[3]*2
    if len(color) == 6:
        color = color + 'FF'

    return color.upper()

class GanttChart(object):
    """
    A Gantt chart realized via Google Charts api.

    A short tour: First, initialize the GanttChart.

    >>> import datetime
    >>> gc = GanttChart('Test Chart', width=800, height=200, progress=(2011,02,15))


    Optionally, you can create categories, which will give your chart a legend and
    make things more colorful.

    >>> complete = GanttCategory('Complete', '000')
    >>> on_time = GanttCategory('On Time', '0c0')
    >>> late = GanttCategory('Late', 'c00')
    >>> upcoming = GanttCategory('Upcoming', '00c')
    >>> upcoming.color
    '0000CCFF'


    Each GanttTask represents a bar on the graph, and must be passed enough date
    information to know where it is. That means passing one of start_date or depends_on,
    and one of end_date or duration (always in days). You should add tasks to the GanttChart
    object, in the order you want them to appear.

    >>> t1 = GanttTask('Task 1', start_date=datetime.date(2011, 02, 01), duration=5, category=complete)
    >>> gc.add_task(t1)
    <GanttTask('Task 1', start_date=datetime.date(2011, 2, 1), end_date=datetime.date(2011, 2, 6))>


    You can also append tasks directly to the tasks attribute, or you can use the add_task() method as a
    constructor for a GanttTask to create and add it in one line.

    >>> t2 = GanttTask('Task 2', depends_on=t1, duration=10, category=on_time)
    >>> gc.tasks.append(t2)
    >>> t3 = gc.add_task('Task 3', depends_on=t2, end_date=(2011, 02, 20), category=upcoming)
    >>> t4 = gc.add_task('Task 4', start_date=datetime.date(2011, 02, 01), duration=12, category=late)


    After the tasks are added, you can get the url...

    >>> print gc.get_url()[:70] # Print the first 70 chars only for brevity
    https://chart.googleapis.com/chart?chxt=x,y&chds=0,19&chco=FFFFFF00,00


    ... or you can get the image as a PIL.Image object.  Pass a save_path to gc.get_image to save it to a file.

    >>> i = gc.get_image()
    >>> i.size
    (800, 200)
    >>> i.format
    'PNG'
    """
    def __init__(self, title, **kwargs):
        self.title = title

        self.tasks = kwargs.get('tasks', [])
        self.width = kwargs.get('width', 600)
        self.height = kwargs.get('height', 200)
        progress = kwargs.get('progress', None)

        if progress is not None:
            self.progress = as_date(progress)
        else:
            self.progress = False

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        return "<GanttChart(width=%s, height=%s, progress=%s)>" % (self.width, self.height, self.progress)

    def add_task(self, *args, **kwargs):
        "A shortcut to create and append a task to this chart."
        if len(args) and isinstance(args[0], GanttTask):
            task = args[0]
        else:
            task = GanttTask(*args, **kwargs)

        self.tasks.append(task)
        return task

    def params(self, raw_inputs={}):
        # Compute the bar width for a desired height
        task_size = int((self.height - 50.) / len(self.tasks)) - 4

        # Compute the grid spacing
        axis_step = 100. / self.duration()

        categories = uniquify((t.category for t in self.tasks))
        colors = (c.color for c in categories)


        params = {
                'cht': 'bhs', #Chart Type: Horizontal Bar
                'chco': 'FFFFFF00,' + ','.join(colors), #Colors: Transparent, blue
                'chtt': self.title,
                'chs': '%sx%s' % (int(self.width), int(self.height)), #Chart size
                'chds': '0,%s' % self.duration(), # Data duration
                'chbh': '%s,4,0' % task_size, #Bar Width
                'chg': '%s,0' % axis_step, # Grid size
                'chxt': 'x,y',
                'chxl': '0:|' + '|'.join(self.day_series()) + '|1:|' + '|'.join(t.title for t in reversed(self.tasks)), # Axes labels
        }


        # Add category labels if necessary
        if reduce(lambda acc, c: acc or c.title, categories, False): # If any category has a title
            params['chdl'] = '|' + '|'.join([c.title for c in categories if c.title]) # Legend


        # Add a progress indicator if progress was passed
        if self.progress and self.progress >= self.start_date() and self.progress <= self.end_date():
            days = (self.progress - self.start_date()).days
            params['chm'] = 'r,%s33,0,0,%s' % (DEFAULT_COLOR[:6], float(days) / self.duration())

        params.update(raw_inputs)

        # Hold offsets to each task and the duration of each task in days.
        offsets = [str((t.start_date - self.start_date()).days) for t in self.tasks]
        data = []


        # Create a separate set of data for each color, but preserve order.
        for c in categories:
            zeroed_data = ['0'] * len(self.tasks)

            for i, t in enumerate(self.tasks):
                if t.category != c:
                    continue

                zeroed_data[i] = str(t.duration)
            data.append(zeroed_data)

        params['chd'] = 't:'+','.join(offsets) + '|' + '|'.join([','.join(d) for d in data])

        return params

    def get_url(self):
        "Returns a GET url for simple image embedding"
        params = self.params()

        return GOOGLE_CHARTS_API_URL + '?' + '&'.join(['%s=%s' % (key, params[key]) for key in params])

    def get_image(self, save_path=None):
        """
        Returns a PIL image via a POST to Google Charts, optionally saving it to a path.

        If there is an HTTP Problem, does nothing and returns None.
        """
        try:
            from PIL import Image
        except ImportError:
            try:
                import Image
            except ImportError:
                raise Exception('Please install PIL (Python Imaging Toolkit) to save an image.')

        import cStringIO

        try:
            req = urllib2.Request(url=GOOGLE_CHARTS_API_URL, data=urllib.urlencode(self.params()))
            resp = urllib2.urlopen(req)
        except urllib2.HTTPError as e:
            return None

        imagedata = cStringIO.StringIO(resp.read())
        i = Image.open(imagedata)


        if save_path:
            i.save(save_path)

        return i

    def day_series(self):
        "Get the list of date labels for this chart"
        start_date = self.start_date()
        duration = self.duration() + 1 # Add 1 because we also label 0 days.


        if self.width / duration > 80:
            skip_n_labels = 1
        else:
            skip_n_labels =  int(1. / (float(self.width) / float(duration) / 80.))

        for i in range(duration):
            if i % skip_n_labels == 0:
                yield (start_date + datetime.timedelta(days=i)).strftime('%d/%m')
            else:
                yield ' '

    def start_date(self):
        return min([t.start_date for t in self.tasks])

    def end_date(self):
        return max([t.end_date for t in self.tasks])

    def duration(self):
        return (self.end_date() - self.start_date()).days


class GanttCategory(object):
    """
    Define a category for a GanttTask. These will be color-coded and a legend entry will be created.
    """
    def __init__(self, title, color='4D89F9FF'):
        self.title = title

        # Accept various input formats for color
        if len(color) == 3:
            color = color[0]*2 + color[1]*2 + color[2]*2 + 'FF'
        if len(color) == 4:
            color = color[0]*2 + color[1]*2 + color[2]*2 + color[3]*2
        if len(color) == 6:
            color = color + 'FF'

        self.color = color.upper()

    def __hash__(self):
        return hash((self.title, self.color))

    def __cmp__(self, other):
        "Make identical instances of this behave like one."
        return cmp(hash(self), hash(other))

class GanttTask(object):
    """
    A task in the chart.  There are three ways to specify the position of the 
    task, using keyword arguments (resolved in this order):

    1) A depends_on and a duration
    2) A depends_on and an end_date
    3) A start_date and a duration
    4) A start_date and an end date

    >>> t1 = GanttTask('Test Task 1', start_date=datetime.date(2009, 1, 1), end_date=datetime.date(2009, 2, 1))
    >>> t1.duration
    31

    >>> t2 = GanttTask('Test Task 2', duration=20, depends_on=t1)
    >>> t2.start_date
    datetime.date(2009, 2, 1)
    >>> t2.end_date
    datetime.date(2009, 2, 21)

    Colors can be specified using the usual css/html shorthand, in rgba hex format:

    >>> t3 = GanttTask('Test Task 2', duration=20, depends_on=t1, color='f00')
    >>> t3.category.color
    'FF0000FF'
    >>> t4 = GanttTask('Test Task 2', duration=20, depends_on=t1, color='f00d')
    >>> t4.category.color
    'FF0000DD'
    """

    def __init__(self, title, start_date=None, end_date=None, **kwargs):
        global CATEGORIES

        self.title = title

        color = parse_color(kwargs.get('color', DEFAULT_COLOR))

        # Get or create a category
        self.category = kwargs.get('category', GanttCategory('', color))


        duration = kwargs.get('duration', None)
        depends_on = kwargs.get('depends_on', None)

        # First, compute a start date
        if depends_on is not None:
            if not isinstance(depends_on, GanttTask):
                raise InvalidTaskError("You must specify a GanttTask object to depend on.")

            self.depends_on = depends_on
            self.start_date = self.depends_on.end_date

        elif start_date is not None:
            try:
                self.start_date = as_date(start_date)
            except InvalidDateError:
                raise InvalidTaskError("You must pass a datetime.date object or a tuple as start_date")

        else:
            raise InvalidTaskError("You must specify either depends_on or start_date")

        # Next, an end date and duration
        if duration is not None:
            self.duration = duration
            self.end_date = self.start_date + datetime.timedelta(days=int(duration))

        elif end_date is not None:
            try:
                self.end_date = as_date(end_date)
            except InvalidDateError:
                raise InvalidTaskError("You must pass a datetime.date object or a tuple as end_date")

            self.duration = (self.end_date - self.start_date).days
        else:
            raise InvalidTaskError("You must specify either duration or end_date")

        if self.end_date < self.start_date:
            raise InvalidTaskError("Invalid Task: start_date is later than end_date")

    def __str__(self):
        return self.title

    def __repr__(self):
        return "<GanttTask(%r, start_date=%r, end_date=%r)>" % (self.title, self.start_date, self.end_date)



if __name__ == "__main__":
    import doctest
    doctest.testmod()
