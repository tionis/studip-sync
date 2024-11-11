import argparse
from .studip import StudipSync

def create_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('--auth-method', help='Authentication method', default='cookie')
    parser.add_argument('--browser', help='Browser to use for cookie extraction', default='firefox')
    parser.add_argument('--data-path', "-d",help='Path to data directory', default='.')
    parser.add_argument('--use-git', help='Use git for version control', action='store_true')
    parser.add_argument('--git-commit-message', help='Commit message for git', default='Update files')
    
    subparsers = parser.add_subparsers(dest='command')
    
    # sync subcommand
    subparsers.add_parser('sync')
    
    # select-semester subcommand
    select_parser = subparsers.add_parser('select-semester')
    select_parser.add_argument('semester', nargs='?')
    
    # get-cookie subcommand
    subparsers.add_parser('get-cookie')
    
    return parser

def app():
    parser = create_parser()
    args = parser.parse_args()

    studip_sync = StudipSync(vars(args))
    
    if args.command == 'sync':
        studip_sync.sync()
    elif args.command == 'select-semester':
        studip_sync.select_semester(args.semester)
    elif args.command == 'get-cookie':
        print(studip_sync.get_cookie())
    else:
        parser.print_help()
