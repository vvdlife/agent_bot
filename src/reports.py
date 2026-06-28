import os
import urllib.request
import logging
from datetime import datetime, timezone, timedelta
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend to prevent GUI errors
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch
from PIL import Image
from src import database, google_auth, tools

logger = logging.getLogger(__name__)

# KST Timezone setting
KST = timezone(timedelta(hours=9))

FONT_URL = "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf"
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "assets", "fonts")
# Fallback to a workspace path if src folder is structured differently
if not os.path.exists(os.path.dirname(FONT_DIR)):
    FONT_DIR = os.path.join(os.getcwd(), "src", "assets", "fonts")
    
FONT_PATH = os.path.join(FONT_DIR, "NanumGothic.ttf")

def ensure_korean_font() -> str:
    """
    Ensures NanumGothic.ttf is downloaded and returns its absolute path.
    Downloads from Google Fonts CDN if not present locally.
    """
    if not os.path.exists(FONT_PATH):
        os.makedirs(FONT_DIR, exist_ok=True)
        logger.info(f"Korean font not found at {FONT_PATH}. Downloading from Google Fonts CDN...")
        try:
            # Configure a user-agent to bypass potential blocks
            req = urllib.request.Request(
                FONT_URL, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req) as response, open(FONT_PATH, 'wb') as out_file:
                out_file.write(response.read())
            logger.info("Korean font downloaded successfully.")
        except Exception as e:
            logger.error(f"Failed to download Korean font: {e}", exc_info=True)
    return FONT_PATH

def generate_weekly_report_image(chat_id: int, start_date_str: str = None, end_date_str: str = None) -> str:
    """
    Generates a beautiful weekly dashboard image (1200x800) containing:
    1. Top summary cards (Total Spent, Tasks completed rate, Next week's schedule events count).
    2. Bottom-left: Expenses breakdown by category (Pie chart).
    3. Bottom-right: Tasks status breakdown (Donut chart).
    
    Returns the absolute path of the generated PNG file.
    """
    # 1. Calculate date range if not provided
    now_kst = datetime.now(KST)
    if not end_date_str:
        end_date_str = now_kst.strftime("%Y-%m-%d")
    if not start_date_str:
        start_date_str = (now_kst - timedelta(days=6)).strftime("%Y-%m-%d")
        
    logger.info(f"Generating weekly report for chat {chat_id} from {start_date_str} to {end_date_str}...")

    # 2. Gather data
    # (a) Expense data
    expenses = database.get_expenses(chat_id, start_date_str, end_date_str)
    total_spent = sum(e['amount'] for e in expenses)
    
    cat_summary = {}
    for e in expenses:
        cat = e['category'] or "기타"
        cat_summary[cat] = cat_summary.get(cat, 0) + e['amount']
        
    # (b) Task data
    all_tasks = database.list_tasks()
    recent_tasks = []
    total_pending = 0
    for t in all_tasks:
        if t['status'] == 'pending':
            total_pending += 1
            
        try:
            # Parse created_at (isoformat)
            try:
                # Try parsing the full ISO string (with timezone and microseconds)
                created_dt = datetime.fromisoformat(t['created_at'])
            except ValueError:
                # Fallback to stripping milliseconds if it fails
                created_clean = t['created_at'].split('.')[0]
                created_dt = datetime.fromisoformat(created_clean)
                
            # Make timezone aware if naive
            if created_dt.tzinfo is None:
                # Interpret naive datetimes (e.g. from old KST logic) as KST
                created_dt = created_dt.replace(tzinfo=KST)
            else:
                # Convert timezone-aware datetimes to KST
                created_dt = created_dt.astimezone(KST)
                
            created_date_str = created_dt.strftime("%Y-%m-%d")
            if start_date_str <= created_date_str <= end_date_str:
                recent_tasks.append(t)
        except Exception as ex:
            logger.warning(f"Failed to parse task creation time: {t.get('created_at')} error: {ex}")
            
    recent_completed = sum(1 for t in recent_tasks if t['status'] == 'completed')
    recent_pending = sum(1 for t in recent_tasks if t['status'] == 'pending')
    recent_total = len(recent_tasks)
    completion_rate = (recent_completed / recent_total * 100) if recent_total > 0 else 0.0

    # (c) Google Calendar next week schedule count
    next_week_events_count = 0
    google_linked = False
    try:
        creds = google_auth.get_google_credentials(chat_id)
        if creds:
            google_linked = True
            from googleapiclient.discovery import build
            service = build('calendar', 'v3', credentials=creds)
            
            # KST dates in ISO format with timezone offsets
            min_dt = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
            max_dt = min_dt + timedelta(days=7)
            
            events_result = service.events().list(
                calendarId='primary',
                timeMin=min_dt.isoformat(),
                timeMax=max_dt.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            next_week_events_count = len(events_result.get('items', []))
    except Exception as ex:
        logger.debug(f"Google Calendar count fetch skipped or failed: {ex}")

    # 3. Setup Font and Matplotlib styling
    font_path = ensure_korean_font()
    if os.path.exists(font_path):
        fm.fontManager.addfont(font_path)
        prop = fm.FontProperties(fname=font_path)
        plt.rcParams['font.family'] = prop.get_name()
    plt.rcParams['axes.unicode_minus'] = False

    # 4. Draw Dashboard
    # Color palette
    bg_color = '#0f172a'        # Dark slate 900
    card_color = '#1e293b'      # Slate 800
    text_color = '#ffffff'
    muted_text_color = '#94a3b8' # Slate 400
    
    accent_colors = ['#10b981', '#3b82f6', '#8b5cf6', '#f59e0b', '#ec4899', '#14b8a6', '#64748b']
    
    fig = plt.figure(figsize=(12, 8), facecolor=bg_color)
    gs = gridspec.GridSpec(3, 2, height_ratios=[0.25, 0.05, 0.70], width_ratios=[1, 1])
    
    # --- Title Section (Header) ---
    ax_title = fig.add_subplot(gs[0, :])
    ax_title.set_facecolor(bg_color)
    ax_title.axis('off')
    
    # Draw main title
    ax_title.text(0.02, 0.85, "📋 주간 종합 리포트", fontsize=24, fontweight='bold', color=text_color, va='center')
    # Draw date range
    dt_start = datetime.strptime(start_date_str, "%Y-%m-%d")
    dt_end = datetime.strptime(end_date_str, "%Y-%m-%d")
    period_str = f"기간: {dt_start.strftime('%Y/%m/%d')} ~ {dt_end.strftime('%Y/%m/%d')}"
    ax_title.text(0.02, 0.65, period_str, fontsize=12, color=muted_text_color, va='center')
    
    # Draw 3 Stat Cards manually inside the header area
    # Stat 1: Total Expense
    card1 = FancyBboxPatch((0.02, 0.1), 0.30, 0.45, transform=ax_title.transAxes, facecolor=card_color, edgecolor='none', boxstyle="round,pad=0.03")
    ax_title.add_patch(card1)
    ax_title.text(0.05, 0.38, "💰 주간 총 지출", fontsize=11, color=muted_text_color, transform=ax_title.transAxes)
    ax_title.text(0.05, 0.22, f"{total_spent:,} 원", fontsize=16, fontweight='bold', color='#10b981', transform=ax_title.transAxes)
    
    # Stat 2: Task Completion Rate
    card2 = FancyBboxPatch((0.35, 0.1), 0.30, 0.45, transform=ax_title.transAxes, facecolor=card_color, edgecolor='none', boxstyle="round,pad=0.03")
    ax_title.add_patch(card2)
    ax_title.text(0.38, 0.38, "🎯 이번 주 할 일 완료율", fontsize=11, color=muted_text_color, transform=ax_title.transAxes)
    ax_title.text(0.38, 0.22, f"{completion_rate:.1f}% ({recent_completed}/{recent_total}건)", fontsize=16, fontweight='bold', color='#3b82f6', transform=ax_title.transAxes)
    
    # Stat 3: Next week events
    card3 = FancyBboxPatch((0.68, 0.1), 0.30, 0.45, transform=ax_title.transAxes, facecolor=card_color, edgecolor='none', boxstyle="round,pad=0.03")
    ax_title.add_patch(card3)
    ax_title.text(0.71, 0.38, "📅 다음 주 일정 예정", fontsize=11, color=muted_text_color, transform=ax_title.transAxes)
    if google_linked:
        events_str = f"{next_week_events_count} 건"
        events_color = '#8b5cf6'
    else:
        events_str = "구글 미연동"
        events_color = '#ef4444'
    ax_title.text(0.71, 0.22, events_str, fontsize=16, fontweight='bold', color=events_color, transform=ax_title.transAxes)

    # --- Divider Line ---
    ax_div = fig.add_subplot(gs[1, :])
    ax_div.set_facecolor(bg_color)
    ax_div.axis('off')
    # Draw simple thin line
    ax_div.plot([0, 1], [0.5, 0.5], color='#334155', lw=1, transform=ax_div.transAxes)

    # --- Bottom Left: Expense Pie Chart ---
    ax_expense = fig.add_subplot(gs[2, 0])
    ax_expense.set_facecolor(bg_color)
    
    # Draw Background Card for Expense Chart
    card_bg_exp = FancyBboxPatch((0.02, 0.02), 0.96, 0.96, transform=ax_expense.transAxes, facecolor=card_color, edgecolor='none', boxstyle="round,pad=0.02", zorder=-1)
    ax_expense.add_patch(card_bg_exp)
    
    ax_expense.text(0.08, 0.90, "📊 카테고리별 지출 비율", fontsize=14, fontweight='bold', color=text_color, transform=ax_expense.transAxes)
    
    if total_spent == 0:
        ax_expense.text(0.5, 0.5, "해당 기간 동안 지출 내역이 없습니다. 💸", fontsize=12, color=muted_text_color, ha='center', va='center', transform=ax_expense.transAxes)
        ax_expense.axis('off')
    else:
        # Pie chart inside the card area
        # Sort category values by amount descending
        sorted_cats = sorted(cat_summary.items(), key=lambda x: x[1], reverse=True)
        labels = [c[0] for c in sorted_cats]
        sizes = [c[1] for c in sorted_cats]
        
        # Draw pie
        wedges, texts, autotexts = ax_expense.pie(
            sizes,
            labels=labels,
            autopct='%1.1f%%',
            startangle=140,
            colors=accent_colors[:len(labels)],
            wedgeprops=dict(width=0.4, edgecolor=card_color, linewidth=2), # Donut style
            pctdistance=0.75,
            center=(0.5, 0.45)
        )
        
        # Style labels
        for t in texts:
            t.set_color(text_color)
            t.set_fontsize(10)
        for at in autotexts:
            at.set_color('#ffffff')
            at.set_fontsize(9)
            at.set_weight('bold')
            
        ax_expense.axis('equal') # Equal aspect ratio ensures that pie is drawn as a circle.
        
    # --- Bottom Right: Tasks Donut Chart ---
    ax_tasks = fig.add_subplot(gs[2, 1])
    ax_tasks.set_facecolor(bg_color)
    
    # Draw Background Card for Task Chart
    card_bg_tasks = FancyBboxPatch((0.02, 0.02), 0.96, 0.96, transform=ax_tasks.transAxes, facecolor=card_color, edgecolor='none', boxstyle="round,pad=0.02", zorder=-1)
    ax_tasks.add_patch(card_bg_tasks)
    
    ax_tasks.text(0.08, 0.90, "🎯 이번 주 할 일 완료율 현황", fontsize=14, fontweight='bold', color=text_color, transform=ax_tasks.transAxes)
    
    if recent_total == 0:
        ax_tasks.text(0.5, 0.5, "이번 주에 생성된 할 일이 없습니다. 📋", fontsize=12, color=muted_text_color, ha='center', va='center', transform=ax_tasks.transAxes)
        ax_tasks.axis('off')
    else:
        labels = ['완료', '미완료']
        sizes = [recent_completed, recent_pending]
        colors = ['#10b981', '#f43f5e'] # Emerald (completed) & Rose (pending)
        
        # Filter sizes to prevent pie errors if one is 0
        active_indices = [i for i, size in enumerate(sizes) if size > 0]
        active_labels = [labels[i] for i in active_indices]
        active_sizes = [sizes[i] for i in active_indices]
        active_colors = [colors[i] for i in active_indices]
        
        wedges, texts, autotexts = ax_tasks.pie(
            active_sizes,
            labels=active_labels,
            autopct='%1.1f%%',
            startangle=90,
            colors=active_colors,
            wedgeprops=dict(width=0.4, edgecolor=card_color, linewidth=2),
            pctdistance=0.75,
            center=(0.5, 0.45)
        )
        
        # Style labels
        for t in texts:
            t.set_color(text_color)
            t.set_fontsize(10)
        for at in autotexts:
            at.set_color('#ffffff')
            at.set_fontsize(9)
            at.set_weight('bold')
            
        ax_tasks.axis('equal')
        
        # Inside Donut center, show actual completion number
        ax_tasks.text(0.5, 0.45, f"{recent_completed}/{recent_total}\n완료", fontsize=12, fontweight='bold', color=text_color, ha='center', va='center', transform=ax_tasks.transAxes)

    # 5. Save the report to output file
    # Calculate target week string
    dt_end = datetime.strptime(end_date_str, "%Y-%m-%d")
    year, week, _ = dt_end.isocalendar()
    report_week = f"{year}-W{week:02d}"
    
    reports_dir = os.path.join(os.getcwd(), "data", "reports")
    os.makedirs(reports_dir, exist_ok=True)
    report_filename = f"weekly_report_{chat_id}_{report_week}.png"
    report_path = os.path.join(reports_dir, report_filename)
    
    # Save fig
    plt.tight_layout()
    plt.savefig(report_path, dpi=150, facecolor=bg_color, bbox_inches='tight')
    plt.close(fig)
    
    logger.info(f"Successfully generated weekly report image at {report_path}.")
    return os.path.abspath(report_path)
