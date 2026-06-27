from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import yt_dlp
import os
import uuid
import threading
import time
import json

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

DOWNLOAD_FOLDER = "/tmp/downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

downloads = {}

# Clean old files (Render tmp cleanup)
def cleanup_old_files():
    for f in os.listdir(DOWNLOAD_FOLDER):
        path = os.path.join(DOWNLOAD_FOLDER, f)
        if os.path.getmtime(path) < time.time() - 3600:
            try: os.remove(path)
            except: pass

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/info', methods=['GET'])
def get_info():
    url = request.args.get('url', '')
    if not url:
        return jsonify({'error': 'URL required'}), 400

    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'socket_timeout': 30,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats = []
            seen = set()
            
            for f in info.get('formats', []):
                if f.get('url'):
                    quality = f.get('format_note', '') or f.get('resolution', '') or f'{f.get("height", "?")}p'
                    ext = f.get('ext', 'mp4')
                    filesize = f.get('filesize') or f.get('filesize_approx', 0)
                    
                    key = f"{quality}_{ext}"
                    if key not in seen and quality:
                        seen.add(key)
                        formats.append({
                            'id': f['format_id'],
                            'quality': quality,
                            'ext': ext,
                            'filesize': filesize,
                            'filesize_str': format_size(filesize),
                            'url': f['url'],
                            'has_audio': f.get('acodec', 'none') != 'none',
                        })
            
            formats.sort(key=lambda x: (x['has_audio'], x['filesize']), reverse=True)
            
            return jsonify({
                'title': info.get('title', 'Unknown'),
                'thumbnail': info.get('thumbnail', ''),
                'duration': info.get('duration', 0),
                'duration_str': format_duration(info.get('duration', 0)),
                'uploader': info.get('uploader', info.get('channel', 'Unknown')),
                'view_count': info.get('view_count', 0),
                'like_count': info.get('like_count', 0),
                'comment_count': info.get('comment_count', 0),
                'formats': formats[:20],
            })
    except Exception as e:
        return jsonify({'error': str(e)[:200]}), 400

@app.route('/api/download', methods=['POST'])
def download_video():
    data = request.json
    url = data.get('url', '')
    format_id = data.get('format_id', 'bestvideo+bestaudio/best')
    
    if not url:
        return jsonify({'error': 'URL required'}), 400
    
    download_id = str(uuid.uuid4())[:12]
    filepath = os.path.join(DOWNLOAD_FOLDER, f"{download_id}.%(ext)s")
    
    downloads[download_id] = {
        'status': 'downloading',
        'progress': 0,
        'filepath': None,
        'filename': None,
        'error': None,
    }
    
    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            downloaded = d.get('downloaded_bytes', 0)
            progress = int((downloaded / total) * 100) if total else 0
            downloads[download_id]['progress'] = progress
        elif d['status'] == 'finished':
            downloads[download_id]['status'] = 'processing'
            downloads[download_id]['filepath'] = d['filename']
            downloads[download_id]['filename'] = os.path.basename(d['filename'])
    
    def do_download():
        try:
            ydl_opts = {
                'format': format_id,
                'outtmpl': filepath,
                'progress_hooks': [progress_hook],
                'quiet': True,
                'no_warnings': True,
                'merge_output_format': 'mp4',
                'socket_timeout': 60,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            downloads[download_id]['status'] = 'finished'
        except Exception as e:
            downloads[download_id]['status'] = 'error'
            downloads[download_id]['error'] = str(e)[:200]
    
    thread = threading.Thread(target=do_download)
    thread.daemon = True
    thread.start()
    
    return jsonify({'download_id': download_id})

@app.route('/api/progress/<download_id>')
def get_progress(download_id):
    if download_id in downloads:
        d = downloads[download_id]
        return jsonify({
            'status': d['status'],
            'progress': d['progress'],
            'filename': d.get('filename'),
            'error': d.get('error'),
        })
    return jsonify({'status': 'not_found'}), 404

@app.route('/api/file/<download_id>')
def get_file(download_id):
    if download_id in downloads and downloads[download_id]['status'] == 'finished':
        filepath = downloads[download_id]['filepath']
        filename = downloads[download_id]['filename']
        if os.path.exists(filepath):
            return send_file(filepath, as_attachment=True, download_name=filename)
    return jsonify({'error': 'File not ready or not found'}), 404

@app.route('/api/formats', methods=['GET'])
def get_formats():
    """Return video/audio-only formats separately"""
    url = request.args.get('url', '')
    if not url:
        return jsonify({'error': 'URL required'}), 400

    try:
        ydl_opts = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            video_formats = []
            audio_formats = []
            seen_v = set()
            seen_a = set()
            
            for f in info.get('formats', []):
                if not f.get('url'): continue
                
                has_video = f.get('vcodec', 'none') != 'none'
                has_audio = f.get('acodec', 'none') != 'none'
                quality = f.get('format_note', '') or f.get('resolution', '') or f'{f.get("height", "?")}p'
                ext = f.get('ext', 'mp4')
                filesize = f.get('filesize') or f.get('filesize_approx', 0)
                
                fmt = {
                    'id': f['format_id'],
                    'quality': quality,
                    'ext': ext,
                    'filesize': filesize,
                    'filesize_str': format_size(filesize),
                    'url': f['url'],
                }
                
                key = f"{quality}_{ext}"
                
                if has_video and key not in seen_v:
                    seen_v.add(key)
                    video_formats.append({**fmt, 'type': 'video'})
                
                if has_audio and not has_video and key not in seen_a:
                    seen_a.add(key)
                    audio_formats.append({**fmt, 'type': 'audio'})
            
            return jsonify({
                'video': sorted(video_formats, key=lambda x: x['filesize'], reverse=True)[:10],
                'audio': sorted(audio_formats, key=lambda x: x['filesize'], reverse=True)[:5],
            })
    except Exception as e:
        return jsonify({'error': str(e)[:200]}), 400

def format_size(bytes):
    if not bytes: return 'Unknown'
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024: return f"{bytes:.1f} {unit}"
        bytes /= 1024
    return f"{bytes:.1f} TB"

def format_duration(seconds):
    if not seconds: return '0:00'
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h: return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)