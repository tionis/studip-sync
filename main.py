#!/bin/env python3
import configparser
import argparse
import toml
import os
import platform
import json
import shutil
import subprocess
import requests
import tempfile
import re
import sys

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

class FzfPrompt:
    def __init__(self, executable_path=None):
        if executable_path:
            self.executable_path = executable_path
        elif not shutil.which("fzf") and not executable_path:
            raise SystemError(
                f"Cannot find 'fzf' installed on PATH.")
        else:
            self.executable_path = "fzf"

    def prompt(self, choices=None, fzf_options="", delimiter='\n'):
        # convert a list to a string [ 1, 2, 3 ] => "1\n2\n3"
        choices_str = delimiter.join(map(str, choices))
        selection = []

        with tempfile.NamedTemporaryFile(delete=False) as input_file:
            with tempfile.NamedTemporaryFile(delete=False) as output_file:
                # Create a temp file with list entries as lines
                input_file.write(choices_str.encode('utf-8'))
                input_file.flush()

        # Invoke fzf externally and write to output file
        os.system(
            f"{self.executable_path} {fzf_options} < \"{input_file.name}\" > \"{output_file.name}\"")

        # get selected options
        with open(output_file.name, encoding="utf-8") as f:
            for line in f:
                selection.append(line.strip('\n'))

        os.unlink(input_file.name)
        os.unlink(output_file.name)

        return selection

class StudipConfig:
    configLoc = ".studip-sync.toml"
    studip_host = "studip.uni-passau.de"
    use_git = False
    git_commit_message = "Auto-sync StudIP files"
    current_semester = ""
    data_path = "."
    auth_method = "cookie"
    browser = "firefox"
    cookie = ""

    def load_config(self):
        try:
            with open(self.configLoc, 'rb') as f:
                # read into string
                file_content = f.read().decode('utf-8')
                config = toml.load(file_content)
                # I'm sure there's a better way to do this, but this works for now
                self.studip_host = config.get("studip_host", self.studip_host)
                self.use_git = config.get("use_git", self.use_git)
                self.git_commit_message = config.get("git_commit_message", self.git_commit_message)
                self.current_semester = config.get("current_semester", self.current_semester)
                self.data_path = config.get("data_path", self.data_path)
                self.auth_method = config.get("auth_method", self.auth_method)
                self.browser = config.get("browser", self.browser)
        except FileNotFoundError:
            eprint("Config file not found, using default values")

    def save_config(self):
        with open(self.configLoc, 'w') as f:
            config = {
                "studip_host": self.studip_host,
                "use_git": self.use_git,
                "git_commit_message": self.git_commit_message,
                "current_semester": self.current_semester,
                "data_path": self.data_path,
                "auth_method": self.auth_method,
                "browser": self.browser,
            }
            toml.dump(config, f)

    def __init__(self, configLoc):
        self.configLoc = configLoc
        self.load_config()

    def get_firefox_profile_dir(self): # Get main firefox profile directory
        if platform.system() == "Windows":
            firefox_dir = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "Mozilla", "Firefox", "Profiles")
        elif platform.system() == "Linux":
            firefox_dir = os.path.join(os.path.expanduser("~"), ".mozilla", "firefox")
        elif platform.system() == "Darwin":
            firefox_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Firefox", "Profiles")
        else:
            raise NotImplementedError(f"Platform \"{platform.system()}\" not supported")
        # Reading profiles.ini to get the default profile
        profiles_ini = os.path.join(firefox_dir, "profiles.ini")
        firefoxProfilesConfig = configparser.ConfigParser()
        firefoxProfilesConfig.read(profiles_ini)
        profile = firefoxProfilesConfig["Profile0"]["Path"] # Unsure if this is correct
        return os.path.join(firefox_dir, profile)

    def get_cookie_from_browser(self):
        if self.cookie != "":
            return self.cookie
        if self.browser == "firefox":
            cookieFilePath = os.path.join(self.get_firefox_profile_dir(), "sessionstore-backups", "recovery.jsonlz4")
            # Check if dejsonlz4 is in PATH
            dejsonlz4PathLoc = shutil.which("dejsonlz4")
            if dejsonlz4PathLoc:
                dejsonlz4 = dejsonlz4PathLoc
            else:
                if os.path.exists("dejsonlz4.com"):
                    dejsonlz4 = "./dejsonlz4.com"
                elif os.path.exists("dejsonlz4"):
                    dejsonlz4 = "./dejsonlz4"
                else:
                    raise FileNotFoundError("dejsonlz4 not found")
            studip_host = self.studip_host
            cookies_process = subprocess.run([dejsonlz4, cookieFilePath], capture_output=True)
            cookies = json.loads(cookies_process.stdout)["cookies"]
            for cookie in cookies:
                if cookie["host"] == studip_host and cookie["name"] == "Seminar_Session":
                    self.cookie = cookie['value']
                    return cookie['value']
            raise KeyError("Cookie not found")
        else:
            raise NotImplementedError(f"Browser \"{self.browser}\" not supported")

    def get_cookie(self):
        if self.auth_method == "cookie":
            return self.get_cookie_from_browser()
        else:
            raise NotImplementedError(f"Auth method \"{self.auth_method}\" not supported")

class StudipSync:
    config : StudipConfig
    prefix = "/studip/api.php"
    user_id = ""

    def __init__(self, config: StudipConfig):
        self.config = config

    def get_user_id(self):
        if self.user_id != "":
            return self.user_id
        self.user_id = self.get("/user")["user_id"]
        return self.user_id

    def get_req(self, path):
        path = path.removeprefix(self.prefix)
        url = f"https://{self.config.studip_host}{self.prefix}{path}"
        print(f"GET {url}")
        resp = requests.get(url, headers={"Cookie": f"Seminar_Session={self.config.get_cookie()}"})
        if resp.status_code != 200:
            raise Exception(f"Failed to get {url}: {resp.status_code}")
        return resp

    def get_no_parse(self, path):
        return self.get_req(path).content

    def get(self, path):
        return self.get_req(path).json()

    def get_subfolders(self, folder):
        if "is_readable" in folder and folder["is_readable"]:
            subfolders = self.get(f"/folder/{folder['id']}/subfolders")["collection"]
            folder["files"] = self.get(f"/folder/{folder['id']}/files")["collection"]
            folder["subfolders"] = []
            for subfolder in subfolders:
                folder["subfolders"].append(self.get_subfolders(subfolder))
        else:
            return {"files": [], "subfolders": []}

    def get_courses(self, semester_id=None):
        courses = []
        if semester_id is None:
            raw_courses = self.get(f"/user/{self.get_user_id()}/courses")["collection"]
        else:
            raw_courses = self.get(f"/user/{self.get_user_id()}/courses?semester={semester_id}")["collection"]

        for course in raw_courses.values():
            path = course["modules"]["documents"] if "documents" in course["modules"] else None
            if path is not None:
                course["top_folder"] = self.get(path)

            if "top_folder" in course and "id" in course["top_folder"] and course["top_folder"]["id"]:
                course["top_folder"]["id"] = self.get(f"/folder/{course['top_folder']['id']}/files")["collection"]

            if "top_folder" in course and "subfolders" in course["top_folder"] and course["top_folder"]["subfolders"]:
                subfolders = []
                for subfolder in course["top_folder"]["subfolders"]:
                    subfolders.append(self.get_subfolders(subfolder))
                course["top_folder"]["subfolders"] = subfolders

            courses.append(course)

        return courses

    
    def escape_filename(self, name):
        return name.replace("/", "_")

    def clean_path(self, path):
        # forbidden symbols: "<" ">" ":" "\"" "\\" "|" "?" "*"
        dirty_symbols_regex = r"[<>:\"\\|?*]"
        return re.sub(dirty_symbols_regex, "_", path)

    def update_links(self):
        # Update symlinks
        current_semester_path = os.path.join(self.config.data_path, "this-semester")
        if os.path.exists(current_semester_path):
            print(f"Removing old this-semester directory at {current_semester_path}")
            shutil.rmtree(current_semester_path)
        os.mkdir(current_semester_path)
        courses = self.get_courses(self.config.current_semester)
        for course in list(set([self.escape_filename(course["title"]) for course in courses])):
            os.symlink(os.path.join("..", "archive" , course), os.path.join(current_semester_path, course), target_is_directory=True)
        if self.config.use_git:
            # Count changes in this-semester dir
            changesProcess = subprocess.run(["git", "diff" , "--name-only", "--",current_semester_path], capture_output=True)
            changes = changesProcess.stdout.decode("utf-8").split("\n")
            if len(changes) > 0:
                # Commit changes
                subprocess.run(["git", "add", current_semester_path])
                subprocess.run(["git", "commit", "-m", self.config.git_commit_message]) # IDEA: smarter commit messages?
                subprocess.run(["git", "push"])

    def select_semester(self, semester=None):
        semesters = self.get("/semesters")["collection"]
        semesterNameToMeta = {}
        for val in semesters.values():
            semesterNameToMeta[val["title"]] = val
        if semester is None:
            fzf = FzfPrompt()
            chosen = fzf.prompt(semesterNameToMeta.keys())
            if len(chosen) == 0:
                raise Exception("No semester chosen")
            semester = chosen[0]
        semester_id = semesterNameToMeta[semester]["id"]        
        self.config.current_semester = semester_id
        self.config.save_config()
        self.update_links()

    def get_files(self, folder, parent_path):
        files = {}
        print(json.dumps(folder, indent=4))
        for file in folder["files"]:
            files[f"{parent_path}/{file['name']}"] = file["id"]
        if "subfolders" in folder:
            for subfolder in folder["subfolders"]:
                for key,value in self.get_files(subfolder, f"{parent_path}/{folder['name']}").items():
                    files[key] = value
        return files

    def sync(self):
        courses = self.get_courses(self.config.current_semester)
        files = {}
        for course in courses:
            for key, value in self.get_files(course["top_folder"], course["title"]).items():
                files[key] = value

        for file in files:
            file_path = self.clean_path(os.path.join(self.config.data_path, "archive", file))
            if not os.path.exists(file_path):
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                print(f"Downloading {file} to {file_path}")
                file_meta = self.get(f"/file/{files[file]}")
                if file_meta["is_downloadable"]:
                    with open(file_path, 'wb') as f:
                        f.write(self.get_no_parse(f"/file/{files[file]}/download"))
                else:
                    print(f"File {file} is not downloadable")
                    print(f"Creating placeholder file {file_path}")
                    # Write studip-sync:non-downloadable-file into placeholder file
                    with open(file_path, 'w') as f:
                        f.write("studip-sync:non-downloadable-file")

def create_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', help='Path to config file', default='.studip-sync.toml')
    
    subparsers = parser.add_subparsers(dest='command')
    
    # sync subcommand
    subparsers.add_parser('sync')
    
    # select-semester subcommand
    select_parser = subparsers.add_parser('select-semester')
    select_parser.add_argument('semester', nargs='?')
    
    # get-cookie subcommand
    subparsers.add_parser('get-cookie')
    
    return parser

def main():
    parser = create_parser()
    args = parser.parse_args()
    
    # cd to script dir so relative paths work
    # INFO this assumes that the script lives with the data, remove this to make the script more general?
    os.chdir(os.path.dirname(os.path.realpath(__file__)))

    config = StudipConfig(args.config)
    studip_sync = StudipSync(config)
    
    if args.command == 'sync':
        studip_sync.sync()
    elif args.command == 'select-semester':
        studip_sync.select_semester(args.semester)
    elif args.command == 'get-cookie':
        print(config.get_cookie())
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
