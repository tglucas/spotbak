import argparse
import logging.handlers
import os
import os.path
import sys

import simplejson as json

import boto3
from boto3 import Session
from boto3.dynamodb.conditions import Key, Attr
from botocore.session import Session as BotoCoreSession


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
DDB_TABLE_NAME = 'spotbak_tracks'
ddb_table = boto3_ddb.Table(DDB_TABLE_NAME)


os.environ['SPOTIPY_CLIENT_ID'] = creds.spotify_client_id
os.environ['SPOTIPY_CLIENT_SECRET'] = creds.spotify_client_secret
auth_manager = SpotifyClientCredentials()
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=creds.spotify_client_id,
                                               client_secret=creds.spotify_client_secret,
                                               redirect_uri=creds.spotify_client_uri,
                                               scope="user-library-read"))


log.info(f'Library init complete.')

parser = argparse.ArgumentParser(description='Fetch Spotify content.')
parser.add_argument('-d', '--ddb', action='store_true', help=f"Store result in DynamoDB table '{DDB_TABLE_NAME}'")
parser.add_argument('-j', '--json', action='store_true', help='Print JSON output')
parser.add_argument('-l', '--likes', action='store_true', help='Liked content')

if __name__ == "__main__":
    log.info('Spotify backup tool starting...')
    args = parser.parse_args()
    tracks = list()
    if args.likes:
        log.info('Fetching Spotify Likes...')
        offset = 0
        fetched = 0
        page_size = 0
        fetch_limit = 20
        while True:
            results = None
            try:
                results = sp.current_user_saved_tracks(limit=fetch_limit, offset=offset)
            except SpotifyException:
                log.exception(f'Fetch error on offset {offset}.')
                sys.exit(1)
            items = results['items']
            page_size = len(items)
            fetched += page_size
            log.info(f'Fetched {fetched} results...')
            for idx, item in enumerate(items):
                offset += 1
                tracks.append(item['track'])
            if page_size < fetch_limit:
                break
    if args.ddb:
        ddb_count = ddb_table.item_count
        track_count = len(tracks)
        log.info(f"DynamoDB table '{DDB_TABLE_NAME}' contains {ddb_count} items.")
        if ddb_count > 0:
            log.warning('Not updating existing DynamoDB table data.')
        elif track_count > 0:
            with ddb_table.batch_writer() as batch:
                for track in tracks:
                    batch.put_item(
                        Item={
                            'artist': track['artists'][0]['name'],
                            'track_name': track['name'],
                            'info': track,
                        },
                    )
            log.info(f"Added {track_count} items to DynamoDB table '{DDB_TABLE_NAME}'.")
        else:
            log.warning('No items to add to DynamoDB.')
    if args.json:
        if len(tracks) > 0:
            try:
                print(json.dumps(tracks))
            except TypeError:
                log.exception(f'Cannot JSON dump {str(tracks)}')
        else:
            log.warning('No items for JSON.')
    log.info('Goodbye.')