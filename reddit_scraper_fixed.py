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

async def fetch_past_content(reddit, end_date_str):
    """
    Fetches posts using a time-based chunking approach to bypass Reddit's 1000-post limit.
    
    Reddit API Limitation: The listing endpoints (subreddit.new()) can only return
    a maximum of 1000 posts total. To fetch more posts, we use time-based chunking:
    we repeatedly search for posts in time windows, moving backwards in time.
    """
    try:
        end_timestamp = datetime.strptime(end_date_str, "%Y-%m-%d").timestamp()
        existing_ids = await get_existing_post_ids()
        print(f"-> Found {len(existing_ids)} existing posts. They will be skipped.")

        subreddit = await reddit.subreddit(SUBREDDIT_NAME)
        
        # Start from current time and work backwards
        current_time = datetime.now().timestamp()
        total_new_posts_processed = 0
        
        print(f"\n{'='*60}")
        print(f"FETCHING POSTS FROM {datetime.fromtimestamp(current_time)} TO {end_date_str}")
        print(f"{'='*60}\n")

        while current_time > end_timestamp:
            print(f"\n-> Searching for posts created before {datetime.fromtimestamp(current_time)}...")
            
            # Use search with timestamp filter to get posts in chunks
            # This allows us to bypass the 1000-post listing limit
            query = f"timestamp:..{int(current_time)}"
            
            oldest_in_window = None
            posts_in_window = 0
            
            try:
                # Fetch up to 1000 posts in this time window
                submissions_list = []
                async for submission in subreddit.search(
                    query=query,
                    sort='new',
                    time_filter='all',
                    limit=1000
                ):
                    if submission.created_utc < end_timestamp:
                        # We've gone past our target date
                        print(f"-> Reached end date. Post {submission.id} is from {datetime.fromtimestamp(submission.created_utc)}")
                        break
                    
                    if submission.id not in existing_ids:
                        submissions_list.append(submission)
                        oldest_in_window = submission.created_utc
                    
                    posts_in_window += 1
                
                if not submissions_list:
                    print(f"-> No new posts found in this time window. Moving to earlier time...")
                    # Move back in time by 30 days if no posts found
                    current_time -= (30 * 24 * 60 * 60)
                    continue
                
                print(f"-> Found {len(submissions_list)} new posts to process (out of {posts_in_window} total in window)")
                
                # Process posts in batches
                for i in range(0, len(submissions_list), BATCH_SIZE):
                    batch = submissions_list[i:i + BATCH_SIZE]
                    print(f"\n-> Processing batch {i//BATCH_SIZE + 1} ({len(batch)} posts)...")
                    
                    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
                    tasks = [process_submission_to_memory(sub, semaphore) for sub in batch]
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
                
                # Move to the timestamp just before the oldest post we found
                if oldest_in_window:
                    current_time = oldest_in_window - 1
                    print(f"\n-> Moving search window to before {datetime.fromtimestamp(current_time)}")
                else:
                    # If we processed all posts but they were all duplicates, move back by 1 day
                    current_time -= (24 * 60 * 60)
                
                # If we found fewer than 1000 posts, we've likely exhausted this time range
                if posts_in_window < 1000 and oldest_in_window and oldest_in_window < end_timestamp:
                    print("-> Reached end of available posts.")
                    break
                    
            except Exception as search_err:
                logging.error(f"Error during search: {search_err}")
                print(f"[ERROR] Search failed: {search_err}")
                # Move back by 7 days and try again
                current_time -= (7 * 24 * 60 * 60)
                continue

        print(f"\n{'='*60}")
        print(f"FINISHED: Processed {total_new_posts_processed} new posts total")
        print(f"{'='*60}\n")

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
    async with reddit:
        await fetch_past_content(reddit, date_str)
    print("\nScript finished.")

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())
