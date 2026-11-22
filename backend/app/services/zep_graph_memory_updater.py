"""
Zep graph memory update service
Pushes Agent activity from the simulation into the Zep graph as it happens
"""

import os
import time
import threading
import json
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
from queue import Queue, Empty

from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.zep_graph_memory_updater')


@dataclass
class AgentActivity:
    """An Agent activity record"""
    platform: str           # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str        # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any]
    round_num: int
    timestamp: str
    
    def to_episode_text(self) -> str:
        """
        Convert an activity into a text description that can be sent to Zep
        
        Written as natural language so Zep can extract entities and relations from it
        No simulation-specific prefix is added, which would mislead the graph update
        """
        # Generate a different description for each action type
        action_descriptions = {
            "CREATE_POST": self._describe_create_post,
            "LIKE_POST": self._describe_like_post,
            "DISLIKE_POST": self._describe_dislike_post,
            "REPOST": self._describe_repost,
            "QUOTE_POST": self._describe_quote_post,
            "FOLLOW": self._describe_follow,
            "CREATE_COMMENT": self._describe_create_comment,
            "LIKE_COMMENT": self._describe_like_comment,
            "DISLIKE_COMMENT": self._describe_dislike_comment,
            "SEARCH_POSTS": self._describe_search,
            "SEARCH_USER": self._describe_search_user,
            "MUTE": self._describe_mute,
        }
        
        describe_func = action_descriptions.get(self.action_type, self._describe_generic)
        description = describe_func()
        
        # Return "agent name: description of the activity" directly, with no simulation prefix
        return f"{self.agent_name}: {description}"
    
    def _describe_create_post(self) -> str:
        content = self.action_args.get("content", "")
        if content:
            return f"published a post: \"{content}\""
        return "published a post"
    
    def _describe_like_post(self) -> str:
        """Like a post - includes the post text and its author"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"liked a post by {post_author}: \"{post_content}\""
        elif post_content:
            return f"liked a post: \"{post_content}\""
        elif post_author:
            return f"liked a post by {post_author}"
        return "liked a post"
    
    def _describe_dislike_post(self) -> str:
        """Dislike a post - includes the post text and its author"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"disliked a post by {post_author}: \"{post_content}\""
        elif post_content:
            return f"disliked a post: \"{post_content}\""
        elif post_author:
            return f"disliked a post by {post_author}"
        return "disliked a post"
    
    def _describe_repost(self) -> str:
        """Repost - includes the original post text and its author"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        
        if original_content and original_author:
            return f"reposted a post by {original_author}: \"{original_content}\""
        elif original_content:
            return f"reposted a post: \"{original_content}\""
        elif original_author:
            return f"reposted a post by {original_author}"
        return "reposted a post"
    
    def _describe_quote_post(self) -> str:
        """Quote a post - includes the original text, its author and the quoting comment"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        quote_content = self.action_args.get("quote_content", "") or self.action_args.get("content", "")
        
        base = ""
        if original_content and original_author:
            base = f"quoted a post by {original_author}: \"{original_content}\""
        elif original_content:
            base = f"quoted a post: \"{original_content}\""
        elif original_author:
            base = f"quoted a post by {original_author}"
        else:
            base = "quoted a post"
        
        if quote_content:
            base += f", commenting: \"{quote_content}\""
        return base
    
    def _describe_follow(self) -> str:
        """Follow a user - includes the followed user's name"""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"followed the user \"{target_user_name}\""
        return "followed a user"
    
    def _describe_create_comment(self) -> str:
        """Post a comment - includes the comment and the post it is on"""
        content = self.action_args.get("content", "")
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if content:
            if post_content and post_author:
                return f"commented on a post by {post_author}, \"{post_content}\": \"{content}\""
            elif post_content:
                return f"commented on a post, \"{post_content}\": \"{content}\""
            elif post_author:
                return f"commented on a post by {post_author}: \"{content}\""
            return f"commented: \"{content}\""
        return "posted a comment"
    
    def _describe_like_comment(self) -> str:
        """Like a comment - includes the comment text and its author"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"liked a comment by {comment_author}: \"{comment_content}\""
        elif comment_content:
            return f"liked a comment: \"{comment_content}\""
        elif comment_author:
            return f"liked a comment by {comment_author}"
        return "liked a comment"
    
    def _describe_dislike_comment(self) -> str:
        """Dislike a comment - includes the comment text and its author"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"disliked a comment by {comment_author}: \"{comment_content}\""
        elif comment_content:
            return f"disliked a comment: \"{comment_content}\""
        elif comment_author:
            return f"disliked a comment by {comment_author}"
        return "disliked a comment"
    
    def _describe_search(self) -> str:
        """Search for posts - includes the search keywords"""
        query = self.action_args.get("query", "") or self.action_args.get("keyword", "")
        return f"searched for \"{query}\"" if query else "ran a search"
    
    def _describe_search_user(self) -> str:
        """Search for users - includes the search keywords"""
        query = self.action_args.get("query", "") or self.action_args.get("username", "")
        return f"searched for the user \"{query}\"" if query else "searched for a user"
    
    def _describe_mute(self) -> str:
        """Mute a user - includes the muted user's name"""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"muted the user \"{target_user_name}\""
        return "muted a user"
    
    def _describe_generic(self) -> str:
        # Generate a generic description for an unknown action type
        return f"performed a {self.action_type} action"


class ZepGraphMemoryUpdater:
    """
    Zep graph memory updater
    
    Watches the simulation's actions log and pushes new Agent activity into the Zep graph as it happens.
    Grouped by platform: once BATCH_SIZE activities have accumulated, they are sent to Zep as one batch.
    
    Every meaningful action is pushed, and action_args carries the full context:
    - the text of the liked/disliked post
    - the text of the reposted/quoted post
    - the name of the followed/muted user
    - the text of the liked/disliked comment
    """
    
    # Batch size (how many activities accumulate per platform before sending)
    BATCH_SIZE = 5
    
    # Platform name mapping (for the console display)
    PLATFORM_DISPLAY_NAMES = {
        'twitter': 'World 1',
        'reddit': 'World 2',
    }
    
    # Send interval (seconds), so requests do not fire too quickly
    SEND_INTERVAL = 0.5
    
    # Retry configuration
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # seconds
    
    def __init__(self, graph_id: str, api_key: Optional[str] = None):
        """
        Initialize the updater
        
        Args:
            graph_id: the Zep graph ID
            api_key: the Zep API key (optional; read from the configuration by default)
        """
        self.graph_id = graph_id
        self.api_key = api_key or Config.ZEP_API_KEY
        
        if not self.api_key:
            raise ValueError("ZEP_API_KEY is not configured")
        
        self.client = Zep(api_key=self.api_key)
        
        # The activity queue
        self._activity_queue: Queue = Queue()
        
        # Per-platform activity buffers (each platform sends a batch once BATCH_SIZE is reached)
        self._platform_buffers: Dict[str, List[AgentActivity]] = {
            'twitter': [],
            'reddit': [],
        }
        self._buffer_lock = threading.Lock()
        
        # Control flags
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        
        # Statistics
        self._total_activities = 0  # activities actually queued
        self._total_sent = 0        # batches successfully sent to Zep
        self._total_items_sent = 0  # activities successfully sent to Zep
        self._failed_count = 0      # batches that failed to send
        self._skipped_count = 0     # activities filtered out (DO_NOTHING)
        
        logger.info(f"ZepGraphMemoryUpdater initialized: graph_id={graph_id}, batch_size={self.BATCH_SIZE}")
    
    def _get_platform_display_name(self, platform: str) -> str:
        """Get the platform's display name"""
        return self.PLATFORM_DISPLAY_NAMES.get(platform.lower(), platform)
    
    def start(self):
        """Start the background worker thread"""
        if self._running:
            return
        
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"ZepMemoryUpdater-{self.graph_id[:8]}"
        )
        self._worker_thread.start()
        logger.info(f"ZepGraphMemoryUpdater started: graph_id={self.graph_id}")
    
    def stop(self):
        """Stop the background worker thread"""
        self._running = False
        
        # Send whatever activities are left
        self._flush_remaining()
        
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)
        
        logger.info(f"ZepGraphMemoryUpdater stopped: graph_id={self.graph_id}, "
                   f"total_activities={self._total_activities}, "
                   f"batches_sent={self._total_sent}, "
                   f"items_sent={self._total_items_sent}, "
                   f"failed={self._failed_count}, "
                   f"skipped={self._skipped_count}")
    
    def add_activity(self, activity: AgentActivity):
        """
        Add an Agent activity to the queue
        
        Every meaningful action is queued, including:
        - CREATE_POST (publish a post)
        - CREATE_COMMENT (post a comment)
        - QUOTE_POST (quote a post)
        - SEARCH_POSTS (search for posts)
        - SEARCH_USER (search for a user)
        - LIKE_POST/DISLIKE_POST (like/dislike a post)
        - REPOST (repost)
        - FOLLOW (follow)
        - MUTE (mute)
        - LIKE_COMMENT/DISLIKE_COMMENT (like/dislike a comment)
        
        action_args carries the full context (the post text, user names and so on).
        
        Args:
            activity: the Agent activity record
        """
        # Skip DO_NOTHING activities
        if activity.action_type == "DO_NOTHING":
            self._skipped_count += 1
            return
        
        self._activity_queue.put(activity)
        self._total_activities += 1
        logger.debug(f"Queueing activity for Zep: {activity.agent_name} - {activity.action_type}")
    
    def add_activity_from_dict(self, data: Dict[str, Any], platform: str):
        """
        Add an activity from dictionary data
        
        Args:
            data: the dictionary parsed from actions.jsonl
            platform: the platform name (twitter/reddit)
        """
        # Skip event-type entries
        if "event_type" in data:
            return
        
        activity = AgentActivity(
            platform=platform,
            agent_id=data.get("agent_id", 0),
            agent_name=data.get("agent_name", ""),
            action_type=data.get("action_type", ""),
            action_args=data.get("action_args", {}),
            round_num=data.get("round", 0),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )
        
        self.add_activity(activity)
    
    def _worker_loop(self):
        """Background work loop - sends each platform's activities to Zep in batches"""
        while self._running or not self._activity_queue.empty():
            try:
                # Try to take an activity from the queue (1 second timeout)
                try:
                    activity = self._activity_queue.get(timeout=1)
                    
                    # Add the activity to that platform's buffer
                    platform = activity.platform.lower()
                    with self._buffer_lock:
                        if platform not in self._platform_buffers:
                            self._platform_buffers[platform] = []
                        self._platform_buffers[platform].append(activity)
                        
                        # Check whether that platform has reached the batch size
                        if len(self._platform_buffers[platform]) >= self.BATCH_SIZE:
                            batch = self._platform_buffers[platform][:self.BATCH_SIZE]
                            self._platform_buffers[platform] = self._platform_buffers[platform][self.BATCH_SIZE:]
                            # Release the lock before sending
                            self._send_batch_activities(batch, platform)
                            # Send interval, so requests do not fire too quickly
                            time.sleep(self.SEND_INTERVAL)
                    
                except Empty:
                    pass
                    
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                time.sleep(1)
    
    def _send_batch_activities(self, activities: List[AgentActivity], platform: str):
        """
        Send a batch of activities to the Zep graph (merged into one text)
        
        Args:
            activities: the list of Agent activities
            platform: the platform name
        """
        if not activities:
            return
        
        # Merge the activities into one text, separated by newlines
        episode_texts = [activity.to_episode_text() for activity in activities]
        combined_text = "\n".join(episode_texts)
        
        # Send with retries
        for attempt in range(self.MAX_RETRIES):
            try:
                self.client.graph.add(
                    graph_id=self.graph_id,
                    type="text",
                    data=combined_text
                )
                
                self._total_sent += 1
                self._total_items_sent += len(activities)
                display_name = self._get_platform_display_name(platform)
                logger.info(f"Sent {len(activities)} {display_name} activities to graph {self.graph_id}")
                logger.debug(f"Batch preview: {combined_text[:200]}...")
                return
                
            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(f"Failed to send the batch to Zep (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}")
                    time.sleep(self.RETRY_DELAY * (attempt + 1))
                else:
                    logger.error(f"Failed to send the batch to Zep after {self.MAX_RETRIES} retries: {e}")
                    self._failed_count += 1
    
    def _flush_remaining(self):
        """Send whatever is left in the queue and the buffers"""
        # First drain the queue into the buffers
        while not self._activity_queue.empty():
            try:
                activity = self._activity_queue.get_nowait()
                platform = activity.platform.lower()
                with self._buffer_lock:
                    if platform not in self._platform_buffers:
                        self._platform_buffers[platform] = []
                    self._platform_buffers[platform].append(activity)
            except Empty:
                break
        
        # Then send whatever each platform's buffer still holds (even below BATCH_SIZE)
        with self._buffer_lock:
            for platform, buffer in self._platform_buffers.items():
                if buffer:
                    display_name = self._get_platform_display_name(platform)
                    logger.info(f"Sending the last {len(buffer)} activities for {display_name}")
                    self._send_batch_activities(buffer, platform)
            # Clear every buffer
            for platform in self._platform_buffers:
                self._platform_buffers[platform] = []
    
    def get_stats(self) -> Dict[str, Any]:
        """Fetch the statistics"""
        with self._buffer_lock:
            buffer_sizes = {p: len(b) for p, b in self._platform_buffers.items()}
        
        return {
            "graph_id": self.graph_id,
            "batch_size": self.BATCH_SIZE,
            "total_activities": self._total_activities,  # activities queued in total
            "batches_sent": self._total_sent,            # batches sent successfully
            "items_sent": self._total_items_sent,        # activities sent successfully
            "failed_count": self._failed_count,          # batches that failed to send
            "skipped_count": self._skipped_count,        # activities filtered out (DO_NOTHING)
            "queue_size": self._activity_queue.qsize(),
            "buffer_sizes": buffer_sizes,                # per-platform buffer sizes
            "running": self._running,
        }


class ZepGraphMemoryManager:
    """
    Manages the Zep graph memory updaters of several simulations
    
    Each simulation can have its own updater instance
    """
    
    _updaters: Dict[str, ZepGraphMemoryUpdater] = {}
    _lock = threading.Lock()
    
    @classmethod
    def create_updater(cls, simulation_id: str, graph_id: str) -> ZepGraphMemoryUpdater:
        """
        Create a graph memory updater for a simulation
        
        Args:
            simulation_id: the simulation ID
            graph_id: the Zep graph ID
            
        Returns:
            the ZepGraphMemoryUpdater instance
        """
        with cls._lock:
            # Stop the old one first when it already exists
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
            
            updater = ZepGraphMemoryUpdater(graph_id)
            updater.start()
            cls._updaters[simulation_id] = updater
            
            logger.info(f"Creating the graph memory updater: simulation_id={simulation_id}, graph_id={graph_id}")
            return updater
    
    @classmethod
    def get_updater(cls, simulation_id: str) -> Optional[ZepGraphMemoryUpdater]:
        """Fetch a simulation's updater"""
        return cls._updaters.get(simulation_id)
    
    @classmethod
    def stop_updater(cls, simulation_id: str):
        """Stop and remove a simulation's updater"""
        with cls._lock:
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
                del cls._updaters[simulation_id]
                logger.info(f"Graph memory updater stopped: simulation_id={simulation_id}")
    
    # Flag that stops stop_all from running twice
    _stop_all_done = False
    
    @classmethod
    def stop_all(cls):
        """Stop every updater"""
        # Prevent a second call
        if cls._stop_all_done:
            return
        cls._stop_all_done = True
        
        with cls._lock:
            if cls._updaters:
                for simulation_id, updater in list(cls._updaters.items()):
                    try:
                        updater.stop()
                    except Exception as e:
                        logger.error(f"Failed to stop the updater: simulation_id={simulation_id}, error={e}")
                cls._updaters.clear()
            logger.info("All graph memory updaters stopped")
    
    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        """Fetch the statistics of every updater"""
        return {
            sim_id: updater.get_stats() 
            for sim_id, updater in cls._updaters.items()
        }
