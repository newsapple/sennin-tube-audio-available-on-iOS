from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import httpx
import asyncio
import json
import random
from datetime import datetime

app = FastAPI()

templates = Jinja2Templates(directory="templates")
templates.env.add_extension('jinja2.ext.do')

INVIDIOUS_INSTANCES = [
  "https://yt.omada.cafe"
]

limits = httpx.Limits(max_connections=300, max_keepalive_connections=100)
client_session = httpx.AsyncClient(timeout=10.0, limits=limits, follow_redirects=True)

async def fetch_invidious(endpoint: str, params: dict = None, force_instance: str = None):
    if force_instance:
        instances = [force_instance] + [i for i in INVIDIOUS_INSTANCES if i != force_instance]
    else:
        instances = list(INVIDIOUS_INSTANCES)
        random.shuffle(instances)
    
    last_error = None
    for instance in instances:
        try:
            url = f"{instance.rstrip('/')}/api/v1{endpoint}"
            response = await client_session.get(url, params=params, timeout=6.0)
            response.raise_for_status()
            return response.json()
        except (httpx.TimeoutException, httpx.HTTPStatusError, Exception) as e:
            last_error = e
            continue
    
    raise last_error if last_error else Exception("All instances failed")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = Query(...), page: int = 1, type: str = "video", force_instance: str = Query(None)):
    try:
        search_type = type if type != "short" else "video"
        query_q = q if type != "short" else f"{q} shorts"
        params = {"q": query_q, "page": page, "type": search_type}

        if force_instance:
            data = await fetch_invidious("/search", params, force_instance=force_instance)
        else:
            instances = list(INVIDIOUS_INSTANCES)
            random.shuffle(instances)
            target_instances = instances[:4]
            
            async def fetch_task(instance):
                url = f"{instance.rstrip('/')}/api/v1/search"
                resp = await client_session.get(url, params=params, timeout=4.0)
                resp.raise_for_status()
                return resp.json()

            tasks = [asyncio.create_task(fetch_task(inst)) for inst in target_instances]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            
            data = None
            for task in done:
                try:
                    data = task.result()
                    break
                except:
                    continue
            
            for task in pending:
                task.cancel()
            
            if data is None:
                data = await fetch_invidious("/search", params)

        results = [{
            "type": item.get("type"),
            "videoId": item.get("videoId"),
            "playlistId": item.get("playlistId"),
            "authorId": item.get("authorId"),
            "title": item.get("title"),
            "lengthSeconds": item.get("lengthSeconds"),
            "author": item.get("author"),
            "authorThumbnails": item.get("authorThumbnails"),
            "videoThumbnails": item.get("videoThumbnails"),
            "viewCountText": item.get("viewCountText"),
            "viewCount": item.get("viewCount"),
            "publishedText": item.get("publishedText"),
            "subCountText": item.get("subCountText"),
            "videoCount": item.get("videoCount")
        } for item in data]
            
        return templates.TemplateResponse("search.html", {
            "request": request, 
            "query": q, 
            "results": results,
            "type": type,
            "page": page
        })
    except httpx.TimeoutException:
        return templates.TemplateResponse("apitimeout.html", {"request": request})
    except Exception:
        return templates.TemplateResponse("apiallerror.html", {"request": request, "instances": INVIDIOUS_INSTANCES})

@app.get("/shorts/{v}", response_class=HTMLResponse)
async def shorts_player(request: Request, v: str, force_instance: str = Query(None)):
    try:
        video_task = fetch_invidious(f"/videos/{v}", force_instance=force_instance)
        comment_task = fetch_invidious(f"/comments/{v}", force_instance=force_instance)
        video_data, comment_data = await asyncio.gather(video_task, comment_task, return_exceptions=True)

        if isinstance(video_data, Exception): raise video_data
        
        format_streams = video_data.get("formatStreams", [])
        if format_streams:
            video_urls = [fmt.get("url") for fmt in format_streams]
        else:
            adaptive = video_data.get("adaptiveFormats", [])
            video_urls = [fmt.get("url") for fmt in adaptive if "video" in fmt.get("type", "")]

        return templates.TemplateResponse("short.html", {
            "request": request,
            "videoid": v,
            "video_title": video_data.get("title"),
            "videourls": video_urls,
            "author": video_data.get("author"),
            "view_count": video_data.get("viewCount", 0),
            "like_count": video_data.get("likeCount", 0),
            "description": video_data.get("descriptionHtml", "").replace("\n", "<br>"),
            "comments": comment_data.get("comments", []) if not isinstance(comment_data, Exception) else []
        })
    except httpx.TimeoutException:
        return templates.TemplateResponse("apitimeout.html", {"request": request})
    except Exception:
        return templates.TemplateResponse("apiallerror.html", {"request": request, "instances": INVIDIOUS_INSTANCES})

@app.get("/watch", response_class=HTMLResponse)
async def watch(request: Request, v: str = Query(...), force_instance: str = Query(None)):
    try:
        async def fetch_video_speculative(vid):
            if force_instance:
                return await fetch_invidious(f"/videos/{vid}", force_instance=force_instance)
            
            instances = list(INVIDIOUS_INSTANCES)
            random.shuffle(instances)
            target_instances = instances[:4]
            
            async def task(instance):
                url = f"{instance.rstrip('/')}/api/v1/videos/{vid}"
                resp = await client_session.get(url, timeout=4.0)
                resp.raise_for_status()
                return resp.json()

            tasks = [asyncio.create_task(task(inst)) for inst in target_instances]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            
            res = None
            for t in done:
                try: res = t.result(); break
                except: continue
            
            for t in pending: t.cancel()
            
            if res is None: res = await fetch_invidious(f"/videos/{vid}")
            return res

        video_task = fetch_video_speculative(v)
        comment_task = fetch_invidious(f"/comments/{v}", force_instance=force_instance)
        video_data, comment_data = await asyncio.gather(video_task, comment_task, return_exceptions=True)

        if isinstance(video_data, Exception): raise video_data
        
        adaptive = video_data.get("adaptiveFormats", [])
        
        audio_url = None
        fallback_url = None
        best_score = -1
        
        for f in adaptive:
            if "audio" in f.get("type", ""):
                if fallback_url is None:
                    fallback_url = f.get("url")
                
                audio_track = f.get("audioTrack", {})
                track_name = audio_track.get("name", "").lower()
                lang = f.get("language", "").lower()
                is_default = audio_track.get("audioIsDefault") is True or f.get("isDefaultAudioTrack") is True
                url_str = f.get("url", "").lower()
                
                track_score = 0
                
                if not audio_track:
                    track_score = 1
                
                if is_default:
                    track_score = 2
                    
                if "original" in track_name or "オリジナル" in track_name or "acont%3doriginal" in url_str:
                    track_score = 3
                
                if (lang.startswith("ja") or lang.startswith("jp") or 
                    "japanese" in track_name or "日本語" in track_name or 
                    "lang%3dja" in url_str or "lang%3djp" in url_str or 
                    "lang=ja" in url_str or "lang=jp" in url_str):
                    track_score = 100
                
                # iPadの <audio> タグで再生不可能なWebM(Opus)を回避するため、
                # M4A(AAC) 形式に対してスコアを大幅に加算し、最優先で取得させる
                if "mp4" in f.get("type", "") or "m4a" in f.get("container", ""):
                    track_score += 50
                
                if track_score > best_score:
                    best_score = track_score
                    audio_url = f.get("url")

        if audio_url is None:
            audio_url = fallback_url

        format_streams = video_data.get("formatStreams", [])
        
        stream_urls = [{
            "url": fmt.get("url"),
            "resolution": fmt.get("qualityLabel"),
            "format": "mp4/mixed",
            "audioUrl": ""
        } for fmt in format_streams]
        
        stream_urls.extend({
            "url": fmt.get("url"),
            "resolution": fmt.get("qualityLabel"),
            "format": "webm/videoOnly",
            "audioUrl": audio_url
        } for fmt in adaptive if "video" in fmt.get("type", "") and "webm" in fmt.get("container", ""))

        default_url = None
        for stream in stream_urls:
            if "720p" in str(stream.get("resolution", "")):
                default_url = stream.get("url")
                break
        
        if not default_url and stream_urls:
            if format_streams:
                default_url = format_streams[0].get("url")
            else:
                default_url = stream_urls[0].get("url")
                
        video_urls = [default_url] if default_url else ([fmt.get("url") for fmt in adaptive if "video" in fmt.get("type", "")])

        recommended = [{
            "video_id": rec.get("videoId"),
            "title": rec.get("title"),
            "author": rec.get("author"),
            "view_count_text": rec.get("viewCountText")
        } for rec in video_data.get("recommendedVideos", [])]

        author_thumbs = video_data.get("authorThumbnails", [])
        author_icon = author_thumbs[-1]["url"] if author_thumbs else ""

        youtube_url = f"https://www.youtube.com/watch?v={v}"

        response = templates.TemplateResponse("watch.html", {
            "request": request,
            "videoid": v,
            "video_title": video_data.get("title"),
            "videourls": video_urls,
            "streamUrls": stream_urls,
            "author": video_data.get("author"),
            "author_id": video_data.get("authorId"),
            "author_icon": author_icon,
            "subscribers_count": video_data.get("subCountText", "非公開"),
            "view_count": video_data.get("viewCount", 0),
            "like_count": video_data.get("likeCount", 0),
            "description": video_data.get("descriptionHtml", "").replace("\n", "<br>"),
            "recommended_videos": recommended,
            "comments": comment_data.get("comments", []) if not isinstance(comment_data, Exception) else [],
            "youtube_url": youtube_url
        })

        try:
            history_json = request.cookies.get("history", "[]")
            history = json.loads(history_json)
            history = [item for item in history if item.get("videoId") != v]
            history.append({
                "videoId": v,
                "title": video_data.get("title"),
                "author": video_data.get("author"),
                "added_at": datetime.now().strftime("%Y-%m-%d %H:%M")
            })
            if len(history) > 50: history = history[-50:]
            response.set_cookie(key="history", value=json.dumps(history), max_age=2592000, httponly=True)
        except:
            pass

        return response

    except httpx.TimeoutException:
        return templates.TemplateResponse("apitimeout.html", {"request": request})
    except Exception:
        return templates.TemplateResponse("apiallerror.html", {"request": request, "instances": INVIDIOUS_INSTANCES})

@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    try:
        history_list = json.loads(request.cookies.get("history", "[]"))
    except:
        history_list = []
    history_list.reverse()
    return templates.TemplateResponse("history.html", {"request": request, "history": history_list})

@app.get("/history/clear")
async def clear_history():
    response = RedirectResponse(url="/history")
    response.delete_cookie("history")
    return response

@app.get("/playlist", response_class=HTMLResponse)
async def playlist(request: Request, list: str = Query(...), force_instance: str = Query(None)):
    try:
        data = await fetch_invidious(f"/playlists/{list}", force_instance=force_instance)
        return templates.TemplateResponse("playlist.html", {
            "request": request,
            "title": data.get("title"),
            "playlistId": list,
            "author": data.get("author"),
            "authorId": data.get("authorId"),
            "videos": data.get("videos", []),
            "description": data.get("descriptionHtml", "")
        })
    except httpx.TimeoutException:
        return templates.TemplateResponse("apitimeout.html", {"request": request})
    except Exception:
        return templates.TemplateResponse("apiallerror.html", {"request": request, "instances": INVIDIOUS_INSTANCES})

@app.get("/channel/{ucid}", response_class=HTMLResponse)
async def channel(request: Request, ucid: str, sort_by: str = "newest", tab: str = "videos", force_instance: str = Query(None)):
    try:
        tasks = [
            fetch_invidious(f"/channels/{ucid}", force_instance=force_instance),
            fetch_invidious(f"/channels/{ucid}/videos", {"sort_by": sort_by}, force_instance=force_instance),
            fetch_invidious(f"/channels/{ucid}/shorts", force_instance=force_instance),
            fetch_invidious(f"/channels/{ucid}/playlists", force_instance=force_instance),
            fetch_invidious(f"/channels/{ucid}/community", force_instance=force_instance)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        channel_data = results[0] if not isinstance(results[0], Exception) else {}
        videos_data = results[1] if not isinstance(results[1], Exception) else {}
        shorts_data = results[2] if not isinstance(results[2], Exception) else {}
        playlists_data = results[3] if not isinstance(results[3], Exception) else {}
        community_data = results[4] if not isinstance(results[4], Exception) else {}

        if isinstance(videos_data, list):
            final_videos = videos_data
        elif isinstance(videos_data, dict):
            final_videos = videos_data.get("videos", [])
        else:
            final_videos = []

        if isinstance(shorts_data, list):
            final_shorts = shorts_data
        elif isinstance(shorts_data, dict):
            final_shorts = shorts_data.get("videos", [])
        else:
            final_shorts = []

        playlists = []
        for pl in playlists_data.get("playlists", []) if isinstance(playlists_data, dict) else (playlists_data if isinstance(playlists_data, list) else []):
            thumb = pl.get("playlistThumbnail", "")
            if thumb and not thumb.startswith("http"):
                thumb = f"https://img.youtube.com/vi/{thumb}/mqdefault.jpg"
            playlists.append({
                "id": pl.get("playlistId", ""),
                "title": pl.get("title", ""),
                "video_count": pl.get("videoCount", 0),
                "thumbnail": thumb,
            })

        author_name = channel_data.get("author")
        author_icon = channel_data.get("authorThumbnails", [{"url": ""}])[-1]["url"] if channel_data.get("authorThumbnails") else ""

        comments_list = community_data.get("comments", []) if isinstance(community_data, dict) else (community_data if isinstance(community_data, list) else [])
        community = [{
            "id": post.get("commentId", ""),
            "content": post.get("contentHtml", "").replace("\n", "<br>"),
            "published_text": post.get("publishedText", ""),
            "likes": post.get("likeCount", 0),
            "author": author_name,
            "author_icon": author_icon,
        } for post in comments_list]

        return templates.TemplateResponse("channel.html", {
            "request": request,
            "ucid": ucid,
            "author": author_name,
            "author_icon": author_icon,
            "sub_count": channel_data.get("subCountText", "非公開"),
            "description": channel_data.get("descriptionHtml", ""),
            "videos": final_videos,
            "shorts": final_shorts,
            "playlists": playlists,
            "community": community,
            "sort_by": sort_by,
            "tab": tab
        })
    except httpx.TimeoutException:
        return templates.TemplateResponse("apitimeout.html", {"request": request})
    except Exception:
        return templates.TemplateResponse("apiallerror.html", {"request": request, "instances": INVIDIOUS_INSTANCES})

@app.get("/suggest")
async def suggest(keyword: str):
    instances = list(INVIDIOUS_INSTANCES)
    random.shuffle(instances)
    for instance in instances:
        try:
            resp = await client_session.get(f"{instance.rstrip('/')}/api/v1/search/suggestions", params={"q": keyword}, timeout=1.5)
            if resp.status_code == 200:
                return resp.json().get("suggestions", [])
        except: continue
    return []

@app.get("/proxy/thumb")
async def proxy_thumb(v: str):
    thumb_url = f"https://i.ytimg.com/vi/{v}/mqdefault.jpg"
    try:
        resp = await client_session.get(thumb_url, timeout=4.0)
        return Response(content=resp.content, media_type="image/jpeg")
    except: return Response(status_code=404)

@app.get("/thumbnail")
async def thumbnail(v: str):
    return await proxy_thumb(v)

@app.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request):
    return templates.TemplateResponse("subscriptions.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)