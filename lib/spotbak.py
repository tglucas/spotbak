import argparse
from argparse import ArgumentParser
import logging.handlers
import os
import os.path
import sys

import simplejson as json

import boto3
from boto3 import Session
from boto3.dynamodb.conditions import Key, Attr
from botocore.session import Session as BotoCoreSession
from botocore.exceptions import ClientError as bcce


import onepasswordconnectsdk
import sentry_sdk

from pathlib import Path
from onepasswordconnectsdk.client import (
    Client,
    new_client_from_environment
)

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.oauth2 import SpotifyClientCredentials
from spotipy.exceptions import SpotifyException


parser: ArgumentParser = argparse.ArgumentParser(description='Fetch Spotify content.')
output_group = parser.add_mutually_exclusive_group(required=True)
output_group.add_argument('--ddb-backup', action='store_true', help='Store result in DynamoDB')
output_group.add_argument('--ddb-fetch', action='store_true', help='Fetch previously stored result in DynamoDB')
output_group.add_argument('--json', action='store_true', help='Print JSON output')
fetch_group = parser.add_mutually_exclusive_group(required=True)
fetch_group.add_argument('--albums', action='store_true')
fetch_group.add_argument('--artists', action='store_true')
fetch_group.add_argument('--episodes', action='store_true')
fetch_group.add_argument('--playlists', action='store_true')
fetch_group.add_argument('--shows', action='store_true')
fetch_group.add_argument('--top-artists', action='store_true')
fetch_group.add_argument('--top-tracks', action='store_true')
fetch_group.add_argument('--tracks', action='store_true')
parser.add_argument('--no-filter-my-playlists', action='store_true', default=False, help='Fetch all playlist information.')
args = parser.parse_args()


APP_NAME = Path(__file__).stem
AWS_REGION = os.environ['AWS_DEFAULT_REGION']


log = logging.getLogger(APP_NAME)
# do not propagate to console logging
log.propagate = False
formatter = logging.Formatter('%(name)s %(threadName)s [%(levelname)s] %(message)s')
if sys.stdout.isatty():
    stream_handler = logging.StreamHandler(stream=sys.stderr)
    stream_handler.setFormatter(formatter)
    log.addHandler(stream_handler)

#TODO: detect syslog
#syslog_handler = logging.handlers.SysLogHandler(address='/dev/log')
#syslog_handler.setFormatter(formatter)
#log.addHandler(syslog_handler)

log.setLevel(logging.INFO)


# credentials
class CredsConfig:
    sentry_dsn: f'opitem:"Sentry" opfield:{APP_NAME}.dsn' = None # type: ignore
    aws_akid: f'opitem:"AWS" opfield:.username' = None # type: ignore
    aws_sak: f'opitem:"AWS" opfield:.password' = None # type: ignore
    spotify_username: f'opitem:"Spotify" opfield:.username' = None # type: ignore
    spotify_client_id: f'opitem:"Spotify" opfield:dev.id' = None # type: ignore
    spotify_client_secret: f'opitem:"Spotify" opfield:dev.secret' = None # type: ignore
    spotify_client_uri: f'opitem:"Spotify" opfield:dev.uri' = None # type: ignore


# test required variables
try:
    op_connect_server = os.environ['OP_CONNECT_SERVER']
except KeyError:
    default_op_connect_server = 'http://localhost:8080'
    log.debug(f'Environment variable OP_CONNECT_SERVER not specified, using [{default_op_connect_server}].')
    op_connect_server = default_op_connect_server

os.environ['OP_CONNECT_TOKEN']
os.environ['OP_VAULT']
creds_config = CredsConfig()
creds_client: Client = new_client_from_environment(url=op_connect_server)
creds_vaults = creds_client.get_vaults()
for vault in creds_vaults:
    log.info(f"Credential vault {vault.name} contains {vault.items} credentials.")
creds: CredsConfig = onepasswordconnectsdk.load(client=creds_client, config=creds_config)


# Sentry
sentry_sdk.init(dsn=creds.sentry_dsn)


# AWS
try:
    os.environ['AWS_ACCESS_KEY_ID']
    os.environ['AWS_SECRET_ACCESS_KEY']
except KeyError:
    os.environ['AWS_ACCESS_KEY_ID'] = creds.aws_akid
    os.environ['AWS_SECRET_ACCESS_KEY'] = creds.aws_sak

boto_session = BotoCoreSession()
boto3_session = Session(
    aws_access_key_id=creds.aws_akid,
    aws_secret_access_key=creds.aws_sak,
    region_name=AWS_REGION,
    botocore_session=boto_session)

boto3_ddb = boto3.resource('dynamodb')
DDB_TABLE_NAME_PREFIX = 'spotbak_'


os.environ['SPOTIPY_CLIENT_ID'] = creds.spotify_client_id
os.environ['SPOTIPY_CLIENT_SECRET'] = creds.spotify_client_secret
auth_manager = SpotifyClientCredentials()
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=creds.spotify_client_id,
                                               client_secret=creds.spotify_client_secret,
                                               redirect_uri=creds.spotify_client_uri,
                                               scope=[
                                                   "user-library-read",
                                                   "playlist-read-private",
                                                   "user-top-read",
                                                   "user-follow-read"]))


def paginate(method_name, item_name, use_cursor=False, item_key=None, **kwargs):
    return_items = list()
    log.info(f'Fetching {item_name} from Spotify...')
    offset = 0
    fetched = 0
    page_size = 0
    fetch_limit = 20
    last_id = None
    while True:
        results = None
        try:
            paginate_args = {"limit": fetch_limit}
            if use_cursor:
                paginate_args["after"] = last_id
            else:
                paginate_args["offset"] = offset
            call_args = {**kwargs, **paginate_args}
            results = getattr(sp, method_name)(**call_args)
        except SpotifyException:
            log.exception(f'Fetch error on offset {offset}.')
            sys.exit(1)
        if item_key:
            fetched_items = results[item_key]
        else:
            fetched_items = results['items']
        page_size = len(fetched_items)
        fetched += page_size
        log.info(f'Fetched {fetched} results...')
        iter_items = fetched_items
        if item_key:
            iter_items = fetched_items['items']
        for idx, item in enumerate(iter_items):
            offset += 1
            return_items.append(item)
        if use_cursor:
            last_id = fetched_items['cursors']['after']
        if page_size < fetch_limit:
            break
    return return_items


def ddb_count(table_name, item_name):
    ddb_table = boto3_ddb.Table(table_name)
    ddb_count = ddb_table.item_count
    log.info(f"DynamoDB table '{table_name}' contains {ddb_count} {item_name}.")
    return ddb_count


def ddb_fetch(table_name, item_name):
    ddb_table = boto3_ddb.Table(table_name)
    log.info(f"Fetching {item_name} from table DynamoDB '{table_name}'...")
    response = ddb_table.scan()
    return response['Items']


if __name__ == "__main__":
    items = None
    ddb_table_name = None
    item_name = None
    if args.albums:
        item_name='saved albums'
        ddb_table_name = f'{DDB_TABLE_NAME_PREFIX}albums'
        if args.ddb_fetch:
            items = ddb_fetch(table_name=ddb_table_name, item_name=item_name)
        else:
            items = paginate(method_name='current_user_saved_albums', item_name=item_name)
    if args.artists:
        item_name = 'followed artists'
        ddb_table_name = f'{DDB_TABLE_NAME_PREFIX}artists'
        if args.ddb_fetch:
            items = ddb_fetch(table_name=ddb_table_name, item_name=item_name)
        else:
            items = paginate(method_name='current_user_followed_artists', item_name=item_name, use_cursor=True, item_key='artists')
    if args.episodes:
        item_name='saved episodes'
        ddb_table_name = f'{DDB_TABLE_NAME_PREFIX}episodes'
        if args.ddb_fetch:
            items = ddb_fetch(table_name=ddb_table_name, item_name=item_name)
        else:
            items = paginate(method_name='current_user_saved_episodes', item_name=item_name)
    if args.playlists:
        item_name = 'playlists'
        ddb_table_name = f'{DDB_TABLE_NAME_PREFIX}playlists'
        if args.ddb_fetch:
            items = ddb_fetch(table_name=ddb_table_name, item_name=item_name)
        else:
            playlists = list()
            user_id = sp.me()['id']
            fetched_playlists = paginate(method_name='current_user_playlists', item_name=item_name)
            for fetched_playlist in fetched_playlists:
                if fetched_playlist['owner']['id'] == user_id or args.no_filter_my_playlists:
                    log.info(f'Collecting playlist data for user {user_id}...')
                    playlist_id = fetched_playlist['id']
                    log.info(f'Fetching tracks for playlist {playlist_id}...')
                    playlist_items = paginate(method_name='playlist_items', item_name='playlist tracks', playlist_id=playlist_id)
                    log.info(f'Fetched {len(playlist_items)} tracks for playlist {playlist_id}.')
                    fetched_playlist['tracks'] = playlist_items
                    playlists.append(fetched_playlist)
            log.info(f'Fetched {len(playlists)} in total.')
            items = playlists
    if args.shows:
        item_name='saved shows'
        ddb_table_name = f'{DDB_TABLE_NAME_PREFIX}shows'
        if args.ddb_fetch:
            items = ddb_fetch(table_name=ddb_table_name, item_name=item_name)
        else:
            items = paginate(method_name='current_user_saved_shows', item_name=item_name)
    if args.top_artists:
        item_name='top artists'
        ddb_table_name = f'{DDB_TABLE_NAME_PREFIX}top_artists'
        if args.ddb_fetch:
            items = ddb_fetch(table_name=ddb_table_name, item_name=item_name)
        else:
            items = paginate(method_name='current_user_top_artists', item_name=item_name)
    if args.top_tracks:
        item_name='top tracks'
        ddb_table_name = f'{DDB_TABLE_NAME_PREFIX}top_tracks'
        if args.ddb_fetch:
            items = ddb_fetch(table_name=ddb_table_name, item_name=item_name)
        else:
            items = paginate(method_name='current_user_top_tracks', item_name=item_name)
    if args.tracks:
        item_name = 'saved tracks'
        ddb_table_name=f'{DDB_TABLE_NAME_PREFIX}tracks'
        if args.ddb_fetch:
            items = ddb_fetch(table_name=ddb_table_name, item_name=item_name)
        else:
            items = paginate(method_name='current_user_saved_tracks', item_name=item_name)
    if args.ddb_backup:
        ddb_table = boto3_ddb.Table(ddb_table_name)
        ddb_item_count = ddb_count(table_name=ddb_table_name, item_name=item_name)
        item_count = len(items)
        if ddb_item_count > 0:
            log.warning('Not updating existing DynamoDB table data.')
        elif item_count > 0:
            if args.albums:
                pkey = 'album_name'
                okeys = [pkey]
                sub_item_name = 'album'
                sub_item_key = 'name'
            if args.artists:
                pkey = 'artist'
                okeys = [pkey]
                sub_item_name = None
                sub_item_key = 'name'
            if args.episodes:
                pkey = 'episode'
                okeys = [pkey]
                sub_item_name = None
                sub_item_key = 'name'
            if args.playlists:
                pkey = 'playlist_name'
                okeys = [pkey]
                sub_item_name = None
                sub_item_key = 'name'
            if args.shows:
                pkey = 'show_name'
                okeys = [pkey]
                sub_item_name = 'show'
                sub_item_key = 'name'
            if args.top_artists:
                pkey = 'artist_name'
                okeys = [pkey]
                sub_item_name = None
                sub_item_key = 'name'
            if args.top_tracks:
                pkey = 'track_id'
                okeys = [pkey]
                sub_item_name = None
                sub_item_key = 'id'
            if args.tracks:
                pkey = 'track_id'
                okeys = [pkey]
                sub_item_name = 'track'
                sub_item_key = 'id'
            item = None
            try:
                with ddb_table.batch_writer(overwrite_by_pkeys=okeys) as batch:
                    for item in items:
                        if sub_item_name:
                            sub_item = item[sub_item_name]
                        else:
                            sub_item = item
                        batch.put_item(
                            Item={
                                pkey: sub_item[sub_item_key],
                                'info': item,
                            },
                        )
            except (KeyError, bcce):
                log.exception(f'Cannot index {str(item)}')
                raise
            log.info(f"Added {item_count} {item_name} to DynamoDB table '{ddb_table_name}'.")
        else:
            log.warning('No {item_name} to add to DynamoDB.')
    if (args.json or args.ddb_fetch) and items:
        if len(items) > 0:
            try:
                print(json.dumps(items))
            except TypeError:
                log.exception(f'Cannot JSON dump {str(items)}')
        else:
            log.warning('No items for JSON.')
    if not items or (items and len(items) == 0):
        log.warning('Nothing fetched.')
    log.info('Goodbye.')