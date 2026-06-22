import re
import urllib.request
import logging
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from src.tools import web_search

logger = logging.getLogger(__name__)

def is_youtube_url(url: str) -> bool:
    """Checks if the URL is a YouTube link."""
    if not url:
        return False
    youtube_regex = (
        r'(https?://)?(www\.)?'
        r'(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|shorts/|.+/)?([^&=%\?]{11})'
    )
    return bool(re.match(youtube_regex, url))

def extract_youtube_id(url: str) -> str:
    """Extracts the 11-character video ID from a YouTube URL."""
    if not url:
        return None
    
    # Standard YouTube video patterns
    patterns = [
        r'youtu\.be/([^%\?&]+)',
        r'youtube\.com/watch\?v=([^%\?&]+)',
        r'youtube\.com/embed/([^%\?&]+)',
        r'youtube\.com/v/([^%\?&]+)',
        r'youtube\.com/shorts/([^%\?&]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
            
    return None

def fetch_youtube_metadata(video_id: str) -> dict:
    """Fetches video title and description from YouTube using BeautifulSoup."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8.0) as response:
            html_data = response.read()
        soup = BeautifulSoup(html_data, 'html.parser')
        
        # Get title
        title_tag = soup.find("meta", property="og:title")
        title = title_tag["content"] if title_tag else ""
        if not title:
            title_tag = soup.find("title")
            title = title_tag.text if title_tag else ""
            
        # Clean title
        if title.endswith(" - YouTube"):
            title = title[:-10]
            
        # Get description
        desc_tag = soup.find("meta", property="og:description")
        desc = desc_tag["content"] if desc_tag else ""
        
        return {"title": title.strip(), "description": desc.strip()}
    except Exception as e:
        logger.error(f"Failed to fetch YouTube metadata for video {video_id}: {e}", exc_info=True)
        return {"title": f"유튜브 동영상 ({video_id})", "description": ""}

def fetch_youtube_transcript(video_id: str) -> dict:
    """
    Fetches the transcript of a YouTube video using youtube-transcript-api.
    If transcripts are disabled, falls back to fetching metadata and performing a web search.
    """
    try:
        logger.info(f"Fetching transcript for YouTube video {video_id}...")
        # Try fetching Korean transcript, then fallback to English, or any available transcript
        transcript_list = YouTubeTranscriptApi().list(video_id)
        
        try:
            transcript = transcript_list.find_transcript(['ko'])
        except Exception:
            try:
                transcript = transcript_list.find_transcript(['en'])
            except Exception:
                transcript = transcript_list.find_first_transcript()
                
        data = transcript.fetch()
        text = " ".join([item.text for item in data])
        
        # Fetch metadata to get title
        meta = fetch_youtube_metadata(video_id)
        return {
            "title": meta["title"],
            "content": text,
            "is_fallback": False
        }
    except Exception as e:
        logger.warning(f"Failed to fetch transcript for video {video_id}: {e}. Activating fallback...")
        
        # Fallback logic: get metadata
        meta = fetch_youtube_metadata(video_id)
        title = meta["title"]
        description = meta["description"]
        
        # Perform DuckDuckGo web search to find summaries/reviews for background context
        search_results = ""
        try:
            search_query = f"유튜브 {title} 요약 소개 내용"
            search_results = web_search(query=search_query, max_results=3)
        except Exception as se:
            logger.error(f"Web search fallback failed for YouTube video: {se}")
            
        combined_text = (
            f"영상 제목: {title}\n"
            f"영상 설명: {description}\n\n"
            f"[관련 검색 정보]\n{search_results}"
        )
        return {
            "title": title,
            "content": combined_text,
            "is_fallback": True
        }

def fetch_web_article_text(url: str) -> dict:
    """
    Crawls a web article URL, removes boilerplate elements, and returns clean text.
    Also attempts to extract the page title.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8.0) as response:
            html_data = response.read()
            
        soup = BeautifulSoup(html_data, 'html.parser')
        
        # Extract title
        title_tag = soup.find("meta", property="og:title")
        title = title_tag["content"] if title_tag else ""
        if not title:
            title_tag = soup.find("title")
            title = title_tag.text if title_tag else "웹 아티클"
            
        # Clean title
        title = title.strip()
        
        # Remove script and style elements
        for element in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
            element.decompose()
            
        # Common CSS selectors for main article bodies
        content_selectors = [
            'article', 'div#articleBodyContents', 'div#articleBody', 'div.article_body', 
            'div.news_post_body', 'div#newsct_article', 'div.story-content', 'div.article-body',
            'main', 'div.main-content', 'div.content'
        ]
        
        body_text = ""
        for selector in content_selectors:
            target = soup.select_one(selector)
            if target:
                body_text = target.get_text()
                break
                
        # If no common wrappers matched, fallback to soup.get_text()
        if not body_text.strip():
            body_text = soup.get_text()
            
        # Clean text: normalize spaces
        lines = (line.strip() for line in body_text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        clean_text = "\n".join(chunk for chunk in chunks if chunk)
        
        # Limit to first 6000 characters to stay within model safety limits
        if len(clean_text) > 6000:
            clean_text = clean_text[:6000] + "..."
            
        return {
            "title": title,
            "content": clean_text
        }
    except Exception as e:
        logger.error(f"Failed to crawl web article {url}: {e}", exc_info=True)
        raise e
