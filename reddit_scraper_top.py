import asyncpraw
import os
import logging
import aiosqlite
import asyncio
import nest_asyncio
from datetime import datetime

# --- Configuration ---
CLIENT_ID = os.environ.get("CLIENT_ID", "2PcFVGruNb4gmg")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "ZFnoN4ICnxUxCCpZ7f8-PIQbXQE")
USERNAME = os.environ.get("USERNAME", "kylej0212")
PASSWORD = os.environ.get("PASSWORD", "lebanonsucks")
USER_AGENT = "NLP app by /u/kylej0212"
SUBREDDIT_NAME = "CATpreparation"
LOG_FILE = "reddit_scraper.log"
DB_FILE = "reddit_data.db"
MAX_CONCURRENT_TASKS = 10
BATCH_SIZE = 100

# --- Logging Setup ---
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

async def setup_database():
    """Initializes and updates the database schema."""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS posts ("
            "id TEXT PRIMARY KEY, title TEXT, author TEXT, created_utc REAL, "
            "url TEXT, subreddit TEXT, body TEXT)"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS comments ("
            "id TEXT PRIMARY KEY, post_id TEXT, parent_id TEXT, author TEXT, "
            "created_utc REAL, body TEXT, permalink TEXT, "
            "FOREIGN KEY (post_id) REFERENCES posts (id))"
        )

        cursor = await db.execute("PRAGMA table_info(posts)")
        if "body" not in {row[1] for row in await cursor.fetchall()}:
            await db.execute("ALTER TABLE posts ADD COLUMN body TEXT")

        cursor = await db.execute("PRAGMA table_info(comments)")
        if "parent_id" not in {row[1] for row in await cursor.fetchall()}:
            await db.execute("ALTER TABLE comments ADD COLUMN parent_id TEXT")

        await db.commit()
    print("-> Database schema is up to date.")

async def get_existing_post_ids():
    """Gets all post IDs from the DB to prevent re-processing."""
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT id FROM posts")
        return {row[0] for row in await cursor.fetchall()}

def flatten_comments(comment_forest):
    """Recursively flattens a comment forest into a list."""
    comments = []
    try:
        for comment in comment_forest:
            if isinstance(comment, asyncpraw.models.MoreComments):
                continue

            try:
                comments.append(comment)
                if hasattr(comment, "replies") and comment.replies:
                    replies = flatten_comments(comment.replies)
                    comments.extend(replies)
            except (AttributeError, TypeError):
                continue
    except Exception as e:
        logging.warning(f"Error flattening comments: {e}")

    return comments

async def process_submission_to_memory(submission, semaphore):
    async with semaphore:
        post_data = None
        comments_data = []
        try:
            print(f"\n[START] Processing Post ID: {submission.id} into memory...")
            post_data = {
                "id": submission.id,
                "title": submission.title,
                "author": str(submission.author),
                "created_utc": submission.created_utc,
                "url": submission.url,
                "subreddit": submission.subreddit.display_name,
                "body": submission.selftext if submission.is_self else ""
            }

            if submission.num_comments == 0:
                print(f"  -> INFO: Post {submission.id} has 0 comments. Skipping comment fetch.")
                return post_data, comments_data

            print(f"  -> ACTION: Fetching comments for post {submission.id} (num_comments={submission.num_comments})...")
            try:
                await submission.load()

                if not hasattr(submission, "comments") or submission.comments is None:
                    print(f"  -> WARNING: submission.comments missing for post {submission.id}.")
                    return post_data, comments_data

                await submission.comments.replace_more(limit=None)
                comment_list = flatten_comments(submission.comments)

                if not comment_list:
                    print(f"  -> WARNING: No comments retrieved for post {submission.id}.")
                    return post_data, comments_data

                print(f"  -> RESPONSE: Retrieved {len(comment_list)} comment objects for post {submission.id}.")

                for comment in comment_list:
                    if isinstance(comment, asyncpraw.models.MoreComments):
                        continue

                    try:
                        parent_id = getattr(comment, "parent_id", None)
                        comments_data.append(
                            (
                                comment.id,
                                comment.submission.id,
                                parent_id,
                                str(comment.author) if comment.author else "[deleted]",
                                comment.created_utc,
                                comment.body if hasattr(comment, "body") else "",
                                comment.permalink if hasattr(comment, "permalink") else ""
                            )
                        )
                    except AttributeError as attr_err:
                        print(f"  -> WARNING: Comment {getattr(comment, 'id', 'unknown')} missing attribute: {attr_err}. Skipping.")
                        continue
                    except Exception as comment_process_err:
                        print(f"  -> WARNING: Error processing comment {getattr(comment, 'id', 'unknown')}: {comment_process_err}. Skipping.")
                        continue

            except AttributeError as e:
                print(f"  -> WARNING: Could not access comments for post {submission.id}: {e}. Skipping comments.")
                logging.warning(f"AttributeError accessing comments for {submission.id}: {e}")
                return post_data, comments_data
            except Exception as comment_err:
                print(f"  -> WARNING: Error fetching comments for post {submission.id}: {comment_err}. Skipping comments.")
                logging.warning(f"Error fetching comments for {submission.id}: {comment_err}")
                return post_data, comments_data

            print(f"[DONE] In-memory processing for Post: {submission.id} ({len(comments_data)} comments)")
            return post_data, comments_data

        except Exception as e:
            logging.error(f"CRITICAL failure processing submission {submission.id}: {e}")
            print(f"[CRITICAL ERROR] for post {submission.id}: {e}. This post's data (if any) will be saved, but its comments will be skipped.")
            return post_data, []

async def fetch_past_content(reddit, end_date_str, time_filter='all'):
    """
    Fetches TOP posts in batches, processes them in memory, and does bulk DB inserts.
    
    Parameters:
    - time_filter: 'all', 'year', 'month', 'week', 'day' (default: 'all')
    
    NOTE: Reddit's top() endpoint is limited to ~1000 posts maximum, just like new().
    This is a Reddit API restriction.
    """
    try:
        end_timestamp = datetime.strptime(end_date_str, "%Y-%m-%d").timestamp()
        existing_ids = await get_existing_post_ids()
        print(f"-> Found {len(existing_ids)} existing posts. They will be skipped.")
        
        print(f"\n{'='*70}")
        print(f"  Fetching TOP posts (time_filter='{time_filter}')")
        print(f"  NOTE: Reddit API can only fetch up to ~1000 posts per run")
        print(f"{'='*70}\n")

        subreddit = await reddit.subreddit(SUBREDDIT_NAME)
        oldest_post_fullname = None
        total_new_posts_processed = 0
        total_posts_seen = 0

        while True:
            print(f"\n-> Fetching new batch of up to {BATCH_SIZE} TOP posts...")
            params = {"after": oldest_post_fullname} if oldest_post_fullname else {}

            # Changed from .new() to .top() with time_filter
            submissions_in_batch = [
                s async for s in subreddit.top(time_filter=time_filter, limit=BATCH_SIZE, params=params)
            ]
            
            if not submissions_in_batch:
                print("-> No more posts returned by Reddit API.")
                break
            
            total_posts_seen += len(submissions_in_batch)
            print(f"-> Total posts seen so far: {total_posts_seen}")

            oldest_post_in_batch = submissions_in_batch[-1]
            oldest_post_fullname = oldest_post_in_batch.fullname
            oldest_date = datetime.fromtimestamp(oldest_post_in_batch.created_utc)
            print(f"-> Oldest post in batch: {oldest_post_in_batch.id} from {oldest_date}")

            submissions_to_process = [
                s for s in submissions_in_batch
                if s.id not in existing_ids and s.created_utc >= end_timestamp
            ]

            if not submissions_to_process:
                if oldest_post_in_batch.created_utc < end_timestamp:
                    print(f"-> Reached end date ({end_date_str}). Stopping.")
                    break

                all_already_processed = all(s.id in existing_ids for s in submissions_in_batch)
                if all_already_processed:
                    print("-> All posts in this batch are already processed. Continuing...")
                    continue

                print("-> No new posts in this batch (all before end date or already processed). Continuing...")
                continue

            print(f"-> Found {len(submissions_to_process)} new posts. Processing to memory...")
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
            tasks = [process_submission_to_memory(sub, semaphore) for sub in submissions_to_process]
            results = await asyncio.gather(*tasks)

            batch_posts_to_insert = [
                (p["id"], p["title"], p["author"], p["created_utc"], p["url"], p["subreddit"], p["body"])
                for p, _ in results if p
            ]
            batch_comments_to_insert = [c for _, c_list in results if c_list for c in c_list]

            if batch_posts_to_insert:
                total_new_posts_processed += len(batch_posts_to_insert)
                print(f"\n-> ACTION: Writing batch of {len(batch_posts_to_insert)} posts and {len(batch_comments_to_insert)} comments to database...")
                async with aiosqlite.connect(DB_FILE) as db:
                    await db.executemany(
                        "INSERT OR IGNORE INTO posts (id, title, author, created_utc, url, subreddit, body) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        batch_posts_to_insert
                    )
                    if batch_comments_to_insert:
                        await db.executemany(
                            "INSERT OR IGNORE INTO comments (id, post_id, parent_id, author, created_utc, body, permalink) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            batch_comments_to_insert
                        )
                    await db.commit()
                print("-> RESPONSE: Batch write successful.")

                new_ids = {p["id"] for p, _ in results if p}
                existing_ids.update(new_ids)

            if oldest_post_in_batch.created_utc < end_timestamp:
                print(f"-> Reached posts older than end date. Stopping.")
                break
            
            # Reddit API hard limit check
            if total_posts_seen >= 1000:
                print(f"\n{'='*70}")
                print(f"  WARNING: Reached Reddit's ~1000 post listing limit")
                print(f"  Cannot fetch more posts using this method")
                print(f"  Total posts seen: {total_posts_seen}")
                print(f"{'='*70}\n")
                break

        print(f"\n-> Finished fetching. Processed a total of {total_new_posts_processed} new posts.")
        print(f"-> Total posts examined: {total_posts_seen}")

    except ValueError:
        print("[ERROR] Invalid date format. Please use YYYY-MM-DD.")
    except Exception as e:
        logging.error(f"An unexpected top-level error occurred: {e}")
        print(f"[ERROR] An unexpected top-level error occurred: {e}")

async def main():
    """Main asynchronous function."""
    await setup_database()
    print("-> ACTION: Authenticating with Reddit...")
    reddit = asyncpraw.Reddit(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        username=USERNAME,
        password=PASSWORD,
        user_agent=USER_AGENT
    )
    try:
        me = await reddit.user.me()
        print(f"-> RESPONSE: Authentication successful for user: u/{me.name}\n")
    except Exception as e:
        print(f"[ERROR] Authentication failed: {e}")
        return

    date_str = input("Enter the end date (YYYY-MM-DD) to fetch content until: ")
    
    print("\nChoose time filter for TOP posts:")
    print("1. all (all time)")
    print("2. year")
    print("3. month")
    print("4. week")
    print("5. day")
    time_filter_choice = input("Enter choice (1-5, default=1): ").strip() or "1"
    
    time_filter_map = {
        "1": "all",
        "2": "year",
        "3": "month",
        "4": "week",
        "5": "day"
    }
    time_filter = time_filter_map.get(time_filter_choice, "all")
    print(f"\n-> Using time_filter='{time_filter}'")
    
    async with reddit:
        await fetch_past_content(reddit, date_str, time_filter)
    print("\nScript finished.")

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())
