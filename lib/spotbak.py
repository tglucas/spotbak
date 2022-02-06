import argparse
from argparse import ArgumentParser
import logging.handlers
import os
import os.path
import sys

import simplejson as json

import boto3
from boto3 import Session
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

import psycopg2
from psycopg2.errors import DatabaseError as dboops

from pymongo import MongoClient


parser: ArgumentParser = argparse.ArgumentParser(description='Fetch Spotify content.')
output_group = parser.add_mutually_exclusive_group(required=False)
output_group.add_argument('--postgres-backup', action='store_true', help='Store result in Postgres database')
output_group.add_argument('--mongodb-backup', action='store_true', help='Store result in MongoDB')
output_group.add_argument('--s3-backup', action='store_true', help='Store result in S3')
output_group.add_argument('--postgres-get', action='store_true', help='Fetch previously stored result in Postgres database')
output_group.add_argument('--mongodb-get', action='store_true', help='Fetch previously stored result in MongoDB')
fetch_group = parser.add_mutually_exclusive_group(required=True)
fetch_group.add_argument('--albums', action='store_true', help="| jq '[.[] | {artist: .name, genre: .genres}]'")
fetch_group.add_argument('--artists', action='store_true', help="| jq '[.[] | {artist: .album.artists[0].name, album: .album.name, track: [{name: .album.tracks.items[].name, track_number: .album.tracks.items[].track_number}]}]'")
fetch_group.add_argument('--episodes', action='store_true')
fetch_group.add_argument('--playlists', action='store_true', help="| jq '[.[] | {name: .name, tracks: .tracks.total, owner: .owner.display_name, public: .public, collaborative: .collaborative, id: .id}]'")
fetch_group.add_argument('--playlists-tracks', action='store_true', help="| jq '[.[] | {artist: .track.artists[0].name, album: .track.album.name, name: .track.name}]'")
fetch_group.add_argument('--shows', action='store_true', help="| jq '[.[] | {name: .show.name, description: .show.description}]'")
fetch_group.add_argument('--top-artists', action='store_true', help="| jq '[.[] | {artist: .name, genre: .genres}]'")
fetch_group.add_argument('--top-tracks', action='store_true', help="| jq '[.[] | {artist: .artists[0].name, album: .album.name, name: .name}]'")
fetch_group.add_argument('--tracks', action='store_true', help="| jq '[.[] | {artist: .track.artists[0].name, album: .track.album.name, name: .track.name}]'")
parser.add_argument('--no-filter-my-playlists', action='store_true', default=False, help='Fetch all playlist tracks.')
parser.add_argument('--debug', action='store_true', default=False, help='Use debug level logging.')
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

if args.debug:
    log.setLevel(logging.DEBUG)
else:
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
    postgres_ip: f'opitem:"Postgres" opfield:DB.IP' = None # type: ignore
    postgres_user: f'opitem:"Postgres" opfield:.username' = None # type: ignore
    postgres_password: f'opitem:"Postgres" opfield:.password' = None # type: ignore
    mongodb_ip: f'opitem:"MongoDB" opfield:DB.IP' = None # type: ignore
    mongodb_user: f'opitem:"MongoDB" opfield:.username' = None # type: ignore
    mongodb_password: f'opitem:"MongoDB" opfield:.password' = None # type: ignore


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

# TODO: S3

DB_NAME = 'spotbak'
DB_TABLE_NAME_PREFIX = f'{DB_NAME}_'

pg_conn = None
if args.postgres_backup:
    log.debug(f'Opening Postgres DB connection {creds.postgres_user}@{creds.postgres_ip}/{DB_NAME}...')
    pg_conn = psycopg2.connect(
        host=creds.postgres_ip,
        database=DB_NAME,
        user=creds.postgres_user,
        password=creds.postgres_password)

md_conn = None
if args.mongodb_backup:
    log.debug(f'Opening MongoDB connection {creds.mongodb_user}@{creds.mongodb_ip}/{DB_NAME}...')
    db_url = creds.mongodb_ip.replace('__USER__', creds.mongodb_user).replace('__PASSWORD__', creds.mongodb_password)
    md_conn = MongoClient(db_url)

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
                if last_id:
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
        if 'cursors' in fetched_items:
            last_id = fetched_items['cursors']['after']
            if last_id is None:
                break
        elif page_size < fetch_limit:
            break
    return return_items


def pg_execute(c, sql):
    log.debug(sql)
    c.execute(sql)


def pg_create_schema(c, table_name, primary_key):
    pg_execute(c=c, sql=f"create table if not exists {table_name} ({primary_key} serial primary key, spotify_{primary_key} varchar(32), spotify_json jsonb not null, unique(spotify_{primary_key}));")
    pg_execute(c=c, sql=f"CREATE INDEX if not exists spotify_json_idx ON {table_name} USING gin (spotify_json);")


def log_exception(e, item):
    if isinstance(e, bcce):
        error_code = e.response['Error']['Code']
    else:
        error_code = e.__class__.__name__
    structured_data = None
    try:
        structured_data = json.dumps(item)
    except TypeError:
        structured_data = str(item)
    log.warning(f'{error_code} during put of {pkey_value}: {structured_data}', exc_info=True)


if __name__ == "__main__":
    items = None
    db_table_name = None
    item_name = None
    if args.albums:
        item_name='saved albums'
        db_table_name = f'{DB_TABLE_NAME_PREFIX}albums'
        if args.postgres_get:
            pass
        elif args.mongodb_get:
            pass
        else:
            items = paginate(method_name='current_user_saved_albums', item_name=item_name)
    if args.artists:
        item_name = 'followed artists'
        db_table_name = f'{DB_TABLE_NAME_PREFIX}artists'
        if args.postgres_get:
            pass
        elif args.mongodb_get:
            pass
        else:
            items = paginate(method_name='current_user_followed_artists', item_name=item_name, use_cursor=True, item_key='artists')
    if args.episodes:
        item_name='saved episodes'
        db_table_name = f'{DB_TABLE_NAME_PREFIX}episodes'
        if args.postgres_get:
            pass
        elif args.mongodb_get:
            pass
        else:
            items = paginate(method_name='current_user_saved_episodes', item_name=item_name)
    if args.playlists:
        item_name = 'playlists'
        db_table_name = f'{DB_TABLE_NAME_PREFIX}playlists'
        if args.postgres_get:
            pass
        elif args.mongodb_get:
            pass
        else:
            items = list()
            user_id = sp.me()['id']
            playlists = paginate(method_name='current_user_playlists', item_name=item_name)
            for playlist in playlists:
                if playlist['owner']['id'] == user_id or args.no_filter_my_playlists:
                    items.append(playlist)
    if args.playlists_tracks:
        item_name = 'playlists tracks'
        db_table_name = f'{DB_TABLE_NAME_PREFIX}playlists_tracks'
        if args.postgres_get:
            pass
        elif args.mongodb_get:
            pass
        else:
            items = list()
            playlist_count = 0
            user_id = sp.me()['id']
            playlists = paginate(method_name='current_user_playlists', item_name=item_name)
            for playlist in playlists:
                if playlist['owner']['id'] == user_id or args.no_filter_my_playlists:
                    playlist_count += 1
                    log.info(f'Collecting playlist data for user {user_id}...')
                    playlist_id = playlist['id']
                    log.info(f'Fetching tracks for playlist {playlist_id}...')
                    tracks = paginate(method_name='playlist_items', item_name='playlist tracks', playlist_id=playlist_id)
                    log.info(f'Fetched {len(tracks)} tracks for playlist {playlist_id}.')
                    items.extend(tracks)
            log.info(f'{len(items)} tracks fetched across {playlist_count} playlists.')
    if args.shows:
        item_name='saved shows'
        db_table_name = f'{DB_TABLE_NAME_PREFIX}shows'
        if args.postgres_get:
            pass
        elif args.mongodb_get:
            pass
        else:
            items = paginate(method_name='current_user_saved_shows', item_name=item_name)
    if args.top_artists:
        item_name='top artists'
        db_table_name = f'{DB_TABLE_NAME_PREFIX}top_artists'
        if args.postgres_get:
            pass
        elif args.mongodb_get:
            pass
        else:
            items = paginate(method_name='current_user_top_artists', item_name=item_name)
    if args.top_tracks:
        item_name='top tracks'
        db_table_name = f'{DB_TABLE_NAME_PREFIX}top_tracks'
        if args.postgres_get:
            pass
        elif args.mongodb_get:
            pass
        else:
            items = paginate(method_name='current_user_top_tracks', item_name=item_name)
    if args.tracks:
        item_name = 'saved tracks'
        db_table_name=f'{DB_TABLE_NAME_PREFIX}tracks'
        if args.postgres_get:
            pass
        elif args.mongodb_get:
            pass
        else:
            items = paginate(method_name='current_user_saved_tracks', item_name=item_name)
    if args.postgres_backup or args.mongodb_backup:
        db_handle = None
        item_count = len(items)
        if item_count > 0:
            if args.albums:
                pkey = 'album_id'
                sub_item_name = 'album'
                sub_item_key = 'id'
            if args.artists:
                pkey = 'artist_id'
                sub_item_name = None
                sub_item_key = 'id'
            if args.episodes:
                pkey = 'episode_id'
                sub_item_name = None
                sub_item_key = 'id'
            if args.playlists:
                pkey = 'playlist_id'
                sub_item_name = None
                sub_item_key = 'id'
            if args.playlists_tracks:
                pkey = 'track_id'
                sub_item_name = 'track'
                sub_item_key = 'id'
            if args.shows:
                pkey = 'show_id'
                sub_item_name = 'show'
                sub_item_key = 'id'
            if args.top_artists:
                pkey = 'artist_id'
                sub_item_name = None
                sub_item_key = 'id'
            if args.top_tracks:
                pkey = 'track_id'
                sub_item_name = None
                sub_item_key = 'id'
            if args.tracks:
                pkey = 'track_id'
                sub_item_name = 'track'
                sub_item_key = 'id'
            log.info(f"Writing {item_count} {item_name} to DB table '{db_table_name}'...")
            if args.postgres_backup:
                db_handle = pg_conn.cursor()
                pg_create_schema(c=db_handle, table_name=db_table_name, primary_key=pkey)
            elif args.mongodb_backup:
                md_collection = md_conn[DB_NAME]
                db_handle = md_collection[db_table_name]
            item = None
            pkey_value = None
            put_count = 0
            for item in items:
                if sub_item_name:
                    sub_item = item[sub_item_name]
                else:
                    sub_item = item
                if sub_item is None or item is None:
                    if item:
                        log.warning(f'Skipping null values derived from {str(item)}')
                    continue
                pkey_value = sub_item[sub_item_key]
                if pkey_value is None:
                    if item:
                        log.warning(f'Skipping null values derived from {str(item)}')
                    continue
                try:
                    if args.postgres_backup:
                        db_handle.execute(
                            f"INSERT INTO {db_table_name} (spotify_{pkey}, spotify_json) VALUES (%s, %s) ON CONFLICT (spotify_{pkey}) DO NOTHING",
                            (pkey_value, json.dumps(item)))
                    elif args.mongodb_backup:
                        db_handle.insert_one(item)
                    put_count += 1
                    if (put_count % 50 == 0):
                        log.info(f'Inserted {put_count} DB items so far...')
                except bcce as e:
                    log_exception(e=e, item=item)
                except (KeyError, TypeError, dboops) as e: # type: ignore
                    log_exception(e=e, item=item)
                    raise
            log.info(f"Added {put_count} (of {len(items)}) {item_name} to DB table '{db_table_name}'.")
            if args.postgres_backup:
                pg_conn.commit()
                db_handle.close()
            if args.mongodb_backup:
                md_conn.close()
        else:
            log.warning(f'No {item_name} to add to DB.')
    if not args.postgres_backup and not args.mongodb_backup and items:
        if len(items) > 0:
            try:
                print(json.dumps(items))
            except TypeError:
                log.exception(f'Cannot JSON dump {str(items)}')
                raise
        else:
            log.warning('No items for JSON.')
    if not items or (items and len(items) == 0):
        log.warning('Nothing fetched.')
    if pg_conn:
        log.info('Closing database connection...')
        pg_conn.close()
    log.info('Goodbye.')