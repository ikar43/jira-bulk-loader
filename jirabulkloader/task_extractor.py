#-*- coding: UTF-8 -*-

import re
import base64
from urllib2 import Request, urlopen, URLError
import simplejson as json
from task_extractor_exceptions import TaskExtractorTemplateErrorProject, TaskExtractorTemplateErrorJson, TaskExtractorJiraValidationError, TaskExtractorJiraCreationError


class TaskExtractor:

    def __init__(self, jira_url, username, password, options, dry_run = False):
        self.h5_tasks_to_link_to_h4_task = [] # will be used to link h5-tasks to the root task
        self.tmpl_vars = {} # template variables dict
        self.tmpl_json = {}

        self.username = username
        self.password = password

        self.default_params = options
        self.dry_run = dry_run
        self.jira_url = jira_url


    def validate_load(self, task_list):
        """
        It takes the task_list prepared by load() and validate list of assignees and projects.
        """
        assignees = []

        for line in task_list:
            if 'assignee' in line:
                if line['assignee'] not in assignees:
                    assignees.append(line['assignee'])
                    self._validate_user(line['assignee'], self._get_project_or_raise_exception(line))


#####################################################################################
# helpers for validate_load()

    def _get_project_or_raise_exception(self, input_line):
        try:
            return input_line['tmpl_ext']['project']['key']
        except KeyError:
            if 'project' in self.default_params:
                return self.default_params['project']['key']
            else:
                raise TaskExtractorTemplateErrorProject('Missing project key in line: ' + input_line['summary'])

    def _validate_user(self, user, project):
        """
        Checks if a new issue of the project can be assigned to the user.
        http://docs.atlassian.com/jira/REST/latest/#id120417
        """

        full_url = "%s/rest/api/2/user/assignable/search?username=%s&project=%s" % (self.jira_url, user, project)
        try:
            result = json.load(self._jira_request(full_url, None, 'GET'))
        except URLError, e:
            if hasattr(e, 'code'):
                if e.code == 403 or e.code == 401:
                    error_message = "Your username and password are not accepted by Jira."
                    raise TaskExtractorJiraValidationError(error_message)
                else:
                    error_message = "The username '%s' and the project '%s' can not be validated.\nJira response: Error %s, %s" % (user, project, e.code, e.read())
                    raise TaskExtractorJiraValidationError(error_message)
        if len(result) == 0: # the project is okay but username is missing n Jira
            error_message = "ERROR: the username '%s' specified in template can not be validated." % user
            raise TaskExtractorJiraValidationError(error_message)


# end of load() helpers
#####################################################################################


    def load(self, input_text):
        """
        Parse and convert the input_text to a list of tasks
        """
        result = []
        input_text = input_text.lstrip('\n');

        pattern_task = re.compile('^(h5\.|h4\.|#[*#]?)\s+(.+)\s+\*(\w+)\*(?:\s+%(\d{4}-\d\d-\d\d)%)?(?:\s+({.+}))?')
        pattern_description = re.compile('=')
        pattern_vars = re.compile('^\[(\w+)=(.+)\]$')
        pattern_json = re.compile('^{.+}$')

        for line in input_text.splitlines():
                if self.tmpl_vars:
                    line = self._replace_template_vars(line)
                line = line.rstrip()
                match_task = pattern_task.search(line)
                if match_task:
                    result.append(self._make_json_task(match_task))
                elif pattern_description.match(line): # if description
                    result[-1] = self._add_task_description(result[-1], line[1:])
                else:
                    match_vars = pattern_vars.search(line)
                    if match_vars:
                        self.tmpl_vars[match_vars.group(1)] = match_vars.group(2)
                    else:
                        if pattern_json.match(line): # if json
                            self.tmpl_json.update(self._validated_json_loads(line))
                        else:
                            result.append({'text':line})
        return result

#####################################################################################
# several helpers for load()

    def _make_json_task(self, match):
        task_json = {'markup':match.group(1), 'summary':match.group(2), 'assignee':match.group(3)}
        if match.group(4): task_json['duedate'] = match.group(4)
        if not len(self.tmpl_json) == 0:
            task_json['tmpl_ext'] = self.tmpl_json.copy()
        if match.group(5):
             if not 'tmpl_ext' in task_json: task_json['tmpl_ext'] = {}
             task_json['tmpl_ext'].update(self._validated_json_loads(match.group(5)))
        return task_json

    def _add_task_description(self, task_json, input_line):
        if 'description' in task_json:
            task_json['description'] = '\n'.join([task_json['description'], input_line])
        else:
            task_json['description'] = input_line
        return task_json

    def _replace_template_vars(self, input_line):
        for key in self.tmpl_vars:
            input_line = re.sub('\$' + key, self.tmpl_vars[key], input_line)
        return input_line

    def _validated_json_loads(self, input_line):
        result = ''
        try:
            result = json.loads(input_line)
        except json.JSONDecodeError, e:
            raise TaskExtractorTemplateErrorJson(input_line)
        return result

# end of load() helpers
#####################################################################################

    def jira_format(self, task):
        fields = {}

        fields.update(self.default_params)
        if 'tmpl_ext' in task: fields.update(task['tmpl_ext'])
        if 'duedate' in task: fields['duedate'] = task['duedate']
        fields['summary'] = task['summary']
        if 'description' in task: fields['description'] = task['description']
        fields['issuetype'] = {'name':task['issuetype']}
        fields['assignee'] = {'name':task['assignee']}
        if 'parent' in task: fields['parent'] = {'key':task['parent']}

        return {'fields':fields}


    def create_tasks(self, task_list):
        """
        It takes the task_list prepared by load(), creates all tasks
        and compose created tasks summary.
        """

        summary = ''
        h5_task_ext = ''

        for line in task_list:
            if 'markup' in line:
                if line['markup'] == 'h5.':
                    if 'h5_task_key' in vars(): # if new h5 task begins
                        h5_summary_list = self._h5_task_completion(h5_task_key, h5_task_caption, h5_task_desc, h5_task_ext)
                        summary = '\n'.join([summary, h5_summary_list]) if summary else h5_summary_list
                        h5_task_ext = ''
                    h5_task_key, h5_task_caption, h5_task_desc = self._create_h5_task_and_return_key_caption_description(line)
                elif line['markup'][0] == '#':
                    sub_task_caption = self._create_sub_task_and_return_caption(line, h5_task_key)
                    h5_task_ext = '\n'.join([h5_task_ext, sub_task_caption]) if h5_task_ext else sub_task_caption
                elif line['markup'] == 'h4.':
                    h4_task = line
            elif 'text' in line:
                h5_task_ext = '\n'.join([h5_task_ext, line['text']]) if h5_task_ext else line['text']

        if 'h5_task_key' in vars():
            h5_summary_list = self._h5_task_completion(h5_task_key, h5_task_caption, h5_task_desc, h5_task_ext)
            summary = '\n'.join([summary, h5_summary_list]) if summary else h5_summary_list

        if 'h4_task' in vars():
            h4_task_key, h4_task_caption = self._create_h4_task_and_return_key_caption(h4_task)
            summary = ('\n'.join([h4_task_caption, summary]) if summary else h4_task_caption)

        return summary

#####################################################################################
# several helpers for create_tasks()

    def _make_task_caption(self, task_json, task_key):
        return ' '.join([task_json['markup'], task_json['summary'], '(' + task_key + ')'])

    def _h5_task_completion(self, key, caption, desc, ext):
        summary_list = [caption]
        if ext:
            desc = '\n'.join([desc, ext]) if desc else ext
            self.update_issue_desc(key, desc)
        if desc:
            summary_list.append(desc)
        return '\n'.join(summary_list)

    def _create_sub_task_and_return_caption(self, sub_task_json, parent_task_key):
        sub_task_json['parent'] = parent_task_key
        sub_task_json['issuetype'] = 'Sub-task'
        sub_task_key = self.create_issue(sub_task_json)
        return self._make_task_caption(sub_task_json,  sub_task_key)

    def _create_h5_task_and_return_key_caption_description(self, h5_task_json):
        h5_task_json['issuetype'] = 'Task'
        h5_task_key = self.create_issue(h5_task_json)
        self.h5_tasks_to_link_to_h4_task.append(h5_task_key)
        h5_task_caption = self._make_task_caption(h5_task_json,  h5_task_key)
        h5_task_desc = h5_task_json['description'] if 'description' in h5_task_json else None
        return (h5_task_key, h5_task_caption, h5_task_desc)

    def _create_h4_task_and_return_key_caption(self, h4_task_json):
        h4_task_json['issuetype'] = 'Task'
        h4_task_key = self.create_issue(h4_task_json)
        for key in self.h5_tasks_to_link_to_h4_task:
            self.create_link(h4_task_key, key)
        return (h4_task_key, self._make_task_caption(h4_task_json,  h4_task_key))

# end of create_tasks() helpers
#####################################################################################

    def create_issue(self, issue):
        """
        """

        if not self.dry_run:
            try:
                full_url = self.jira_url + '/rest/api/2/issue'
                jira_response = self._jira_request(full_url, json.dumps(self.jira_format(issue)))
                issueID = json.load(jira_response)
                return issueID['key']
            except URLError, e:
                if hasattr(e, 'code'):
                    if e.code == 403 or e.code == 401:
                        error_message = "Your username and password are not accepted by Jira."
                        raise TaskExtractorJiraValidationError(error_message)
                    else:
                        error_message = "ERROR: The task cannot be created: %s\nJira response: Error %s, %s" % (issue['summary'], e.code, e.read())
                        raise TaskExtractorJiraCreationError(error_message)
        else:
            return 'DRY-RUN-XXXX'


    def create_link(self, inward_issue, outward_issue, link_type = 'Inclusion'):
        """Creates an issue link between two issues.

        The specified link type in the request is used to create the link 
        and will create a link from the first issue to the second issue using the outward description.
        The list of issue types can be retrieved using rest/api/2/issueLinkType
        For now possible types are Block, Duplicate, Gantt Dependency, Inclusion, Reference
        """

        if not self.dry_run:
            jira_link = {"type":{"name":link_type},"inwardIssue":{"key":inward_issue},"outwardIssue": {"key": outward_issue}}
            full_url = self.jira_url + '/rest/api/2/issueLink'
            return self._jira_request(full_url, json.dumps(jira_link))
        else:
          return 'dry run'


    def update_issue_desc(self, issue_key, issue_desc):
        if not self.dry_run:
            full_url = self.jira_url + '/rest/api/2/issue/' + issue_key
            jira_data = {'update':{'description':[{'set':issue_desc}]}}
            return self._jira_request(full_url, json.dumps(jira_data), 'PUT')
        else:
            return 'dry run'


    def _jira_request(self, url, data, method = 'POST', headers = {'Content-Type': 'application/json'}):
        """Compose and make HTTP request to JIRA.

        url should be a string containing a valid URL.
        data is a str. headers is dict of HTTP headers.
        Supported method are POST (for creating and linking) and PUT (for updating).
        It expects also self.username and self.password to be set to perform basic HTTP authentication.
        """
        request = Request(url, data, headers)

        # basic HTTP authentication
        base64string = base64.encodestring('%s:%s' % (self.username, self.password)).replace('\n', '')
        request.add_header("Authorization", "Basic %s" % base64string)
        request.get_method = lambda : method

        return urlopen(request)

