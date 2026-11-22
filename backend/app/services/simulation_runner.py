"""
OASIS simulation runner
Runs the simulation in the background and records every Agent action, with live status monitoring
"""

import os
import sys
import json
import time
import asyncio
import threading
import subprocess
import signal
import atexit
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from queue import Queue

from ..config import Config
from ..utils.logger import get_logger
from .zep_graph_memory_updater import ZepGraphMemoryManager
from .simulation_ipc import SimulationIPCClient, CommandType, IPCResponse

logger = get_logger('mirofish.simulation_runner')

# Whether the cleanup function has been registered
_cleanup_registered = False

# Platform detection
IS_WINDOWS = sys.platform == 'win32'


class RunnerStatus(str, Enum):
    """Runner status"""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentAction:
    """An Agent action record"""
    round_num: int
    timestamp: str
    platform: str  # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str  # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    success: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "timestamp": self.timestamp,
            "platform": self.platform,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "action_type": self.action_type,
            "action_args": self.action_args,
            "result": self.result,
            "success": self.success,
        }


@dataclass
class RoundSummary:
    """A per-round summary"""
    round_num: int
    start_time: str
    end_time: Optional[str] = None
    simulated_hour: int = 0
    twitter_actions: int = 0
    reddit_actions: int = 0
    active_agents: List[int] = field(default_factory=list)
    actions: List[AgentAction] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "simulated_hour": self.simulated_hour,
            "twitter_actions": self.twitter_actions,
            "reddit_actions": self.reddit_actions,
            "active_agents": self.active_agents,
            "actions_count": len(self.actions),
            "actions": [a.to_dict() for a in self.actions],
        }


@dataclass
class SimulationRunState:
    """The live simulation run status"""
    simulation_id: str
    runner_status: RunnerStatus = RunnerStatus.IDLE
    
    # Progress information
    current_round: int = 0
    total_rounds: int = 0
    simulated_hours: int = 0
    total_simulation_hours: int = 0
    
    # Per-platform round and simulation time (for the dual-platform display)
    twitter_current_round: int = 0
    reddit_current_round: int = 0
    twitter_simulated_hours: int = 0
    reddit_simulated_hours: int = 0
    
    # Platform status
    twitter_running: bool = False
    reddit_running: bool = False
    twitter_actions_count: int = 0
    reddit_actions_count: int = 0
    
    # Platform completion flags (set when a simulation_end event shows up in actions.jsonl)
    twitter_completed: bool = False
    reddit_completed: bool = False
    
    # Per-round summaries
    rounds: List[RoundSummary] = field(default_factory=list)
    
    # Recent actions (shown live in the frontend)
    recent_actions: List[AgentAction] = field(default_factory=list)
    max_recent_actions: int = 50
    
    # Timestamp
    started_at: Optional[str] = None
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    
    # Error information
    error: Optional[str] = None
    
    # Process ID (used to stop it)
    process_pid: Optional[int] = None
    
    def add_action(self, action: AgentAction):
        """Add an action to the recent actions list"""
        self.recent_actions.insert(0, action)
        if len(self.recent_actions) > self.max_recent_actions:
            self.recent_actions = self.recent_actions[:self.max_recent_actions]
        
        if action.platform == "twitter":
            self.twitter_actions_count += 1
        else:
            self.reddit_actions_count += 1
        
        self.updated_at = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "runner_status": self.runner_status.value,
            "current_round": self.current_round,
            "total_rounds": self.total_rounds,
            "simulated_hours": self.simulated_hours,
            "total_simulation_hours": self.total_simulation_hours,
            "progress_percent": round(self.current_round / max(self.total_rounds, 1) * 100, 1),
            # Per-platform round and time
            "twitter_current_round": self.twitter_current_round,
            "reddit_current_round": self.reddit_current_round,
            "twitter_simulated_hours": self.twitter_simulated_hours,
            "reddit_simulated_hours": self.reddit_simulated_hours,
            "twitter_running": self.twitter_running,
            "reddit_running": self.reddit_running,
            "twitter_completed": self.twitter_completed,
            "reddit_completed": self.reddit_completed,
            "twitter_actions_count": self.twitter_actions_count,
            "reddit_actions_count": self.reddit_actions_count,
            "total_actions_count": self.twitter_actions_count + self.reddit_actions_count,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "process_pid": self.process_pid,
        }
    
    def to_detail_dict(self) -> Dict[str, Any]:
        """Detailed information including the recent actions"""
        result = self.to_dict()
        result["recent_actions"] = [a.to_dict() for a in self.recent_actions]
        result["rounds_count"] = len(self.rounds)
        return result


class SimulationRunner:
    """
    Simulation runner
    
    Responsibilities:
    1. Run the OASIS simulation in a background process
    2. Parse the run log and record every Agent action
    3. Provide a live status query interface
    4. Support pause/stop/resume
    """
    
    # Directory where run state is stored
    RUN_STATE_DIR = Config.OASIS_SIMULATION_DATA_DIR
    
    # The scripts directory
    SCRIPTS_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../scripts'
    )
    
    # In-memory run state
    _run_states: Dict[str, SimulationRunState] = {}
    _processes: Dict[str, subprocess.Popen] = {}
    _action_queues: Dict[str, Queue] = {}
    _monitor_threads: Dict[str, threading.Thread] = {}
    _stdout_files: Dict[str, Any] = {}  # stdout file handles
    _stderr_files: Dict[str, Any] = {}  # stderr file handles
    
    # Graph memory update configuration
    _graph_memory_enabled: Dict[str, bool] = {}  # simulation_id -> enabled
    
    @classmethod
    def get_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """Fetch the run status"""
        if simulation_id in cls._run_states:
            return cls._run_states[simulation_id]
        
        # Try loading it from disk
        state = cls._load_run_state(simulation_id)
        if state:
            cls._run_states[simulation_id] = state
        return state
    
    @classmethod
    def _load_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """Load the run status from disk"""
        state_file = os.path.join(cls.RUN_STATE_DIR, simulation_id, "run_state.json")
        if not os.path.exists(state_file):
            return None
        
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            state = SimulationRunState(
                simulation_id=simulation_id,
                runner_status=RunnerStatus(data.get("runner_status", "idle")),
                current_round=data.get("current_round", 0),
                total_rounds=data.get("total_rounds", 0),
                simulated_hours=data.get("simulated_hours", 0),
                total_simulation_hours=data.get("total_simulation_hours", 0),
                # Per-platform round and time
                twitter_current_round=data.get("twitter_current_round", 0),
                reddit_current_round=data.get("reddit_current_round", 0),
                twitter_simulated_hours=data.get("twitter_simulated_hours", 0),
                reddit_simulated_hours=data.get("reddit_simulated_hours", 0),
                twitter_running=data.get("twitter_running", False),
                reddit_running=data.get("reddit_running", False),
                twitter_completed=data.get("twitter_completed", False),
                reddit_completed=data.get("reddit_completed", False),
                twitter_actions_count=data.get("twitter_actions_count", 0),
                reddit_actions_count=data.get("reddit_actions_count", 0),
                started_at=data.get("started_at"),
                updated_at=data.get("updated_at", datetime.now().isoformat()),
                completed_at=data.get("completed_at"),
                error=data.get("error"),
                process_pid=data.get("process_pid"),
            )
            
            # Load the recent actions
            actions_data = data.get("recent_actions", [])
            for a in actions_data:
                state.recent_actions.append(AgentAction(
                    round_num=a.get("round_num", 0),
                    timestamp=a.get("timestamp", ""),
                    platform=a.get("platform", ""),
                    agent_id=a.get("agent_id", 0),
                    agent_name=a.get("agent_name", ""),
                    action_type=a.get("action_type", ""),
                    action_args=a.get("action_args", {}),
                    result=a.get("result"),
                    success=a.get("success", True),
                ))
            
            return state
        except Exception as e:
            logger.error(f"Failed to load the run status: {str(e)}")
            return None
    
    @classmethod
    def _save_run_state(cls, state: SimulationRunState):
        """Save the run status to disk"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, state.simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        state_file = os.path.join(sim_dir, "run_state.json")
        
        data = state.to_detail_dict()
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        cls._run_states[state.simulation_id] = state
    
    @classmethod
    def start_simulation(
        cls,
        simulation_id: str,
        platform: str = "parallel",  # twitter / reddit / parallel
        max_rounds: int = None,  # maximum rounds (optional, trims very long runs)
        enable_graph_memory_update: bool = False,  # push activity into the Zep graph
        graph_id: str = None  # the Zep graph ID (required when graph updates are enabled)
    ) -> SimulationRunState:
        """
        Start the simulation
        
        Args:
            simulation_id: the simulation ID
            platform: the run platform (twitter/reddit/parallel)
            max_rounds: maximum rounds (optional, trims very long runs)
            enable_graph_memory_update: push Agent activity into the Zep graph as it happens
            graph_id: the Zep graph ID (required when graph updates are enabled)
            
        Returns:
            SimulationRunState
        """
        # Check whether it is already running
        existing = cls.get_run_state(simulation_id)
        if existing and existing.runner_status in [RunnerStatus.RUNNING, RunnerStatus.STARTING]:
            raise ValueError(f"The simulation is already running: {simulation_id}")
        
        # Load the simulation configuration
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            raise ValueError(f"The simulation configuration does not exist; call /prepare first")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # Initialize the run state
        time_config = config.get("time_config", {})
        total_hours = time_config.get("total_simulation_hours", 72)
        minutes_per_round = time_config.get("minutes_per_round", 30)
        total_rounds = int(total_hours * 60 / minutes_per_round)
        
        # Truncate the round count when a maximum was given
        if max_rounds is not None and max_rounds > 0:
            original_rounds = total_rounds
            total_rounds = min(total_rounds, max_rounds)
            if total_rounds < original_rounds:
                logger.info(f"Rounds truncated: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")
        
        state = SimulationRunState(
            simulation_id=simulation_id,
            runner_status=RunnerStatus.STARTING,
            total_rounds=total_rounds,
            total_simulation_hours=total_hours,
            started_at=datetime.now().isoformat(),
        )
        
        cls._save_run_state(state)
        
        # Create the updater when graph memory updates are enabled
        if enable_graph_memory_update:
            if not graph_id:
                raise ValueError("graph_id is required when graph memory updates are enabled")
            
            try:
                ZepGraphMemoryManager.create_updater(simulation_id, graph_id)
                cls._graph_memory_enabled[simulation_id] = True
                logger.info(f"Graph memory updates enabled: simulation_id={simulation_id}, graph_id={graph_id}")
            except Exception as e:
                logger.error(f"Failed to create the graph memory updater: {e}")
                cls._graph_memory_enabled[simulation_id] = False
        else:
            cls._graph_memory_enabled[simulation_id] = False
        
        # Work out which script to run (the scripts live in backend/scripts/)
        if platform == "twitter":
            script_name = "run_twitter_simulation.py"
            state.twitter_running = True
        elif platform == "reddit":
            script_name = "run_reddit_simulation.py"
            state.reddit_running = True
        else:
            script_name = "run_parallel_simulation.py"
            state.twitter_running = True
            state.reddit_running = True
        
        script_path = os.path.join(cls.SCRIPTS_DIR, script_name)
        
        if not os.path.exists(script_path):
            raise ValueError(f"The script does not exist: {script_path}")
        
        # Create the action queue
        action_queue = Queue()
        cls._action_queues[simulation_id] = action_queue
        
        # Start the simulation process
        try:
            # Build the run command with full paths
            # The new log layout:
            #   twitter/actions.jsonl - the Twitter action log
            #   reddit/actions.jsonl  - the Reddit action log
            #   simulation.log        - the main process log
            
            cmd = [
                sys.executable,  # the Python interpreter
                script_path,
                "--config", config_path,  # the full configuration file path
            ]
            
            # Add the argument when a maximum round count was given
            if max_rounds is not None and max_rounds > 0:
                cmd.extend(["--max-rounds", str(max_rounds)])
            
            # Create the main log file so a full stdout/stderr pipe cannot block the process
            main_log_path = os.path.join(sim_dir, "simulation.log")
            main_log_file = open(main_log_path, 'w', encoding='utf-8')
            
            # Set the child process environment so Windows uses UTF-8
            # This fixes third-party libraries (such as OASIS) that read files without specifying an encoding
            env = os.environ.copy()
            env['PYTHONUTF8'] = '1'  # Python 3.7+: every open() defaults to UTF-8
            env['PYTHONIOENCODING'] = 'utf-8'  # make stdout/stderr UTF-8
            
            # Set the working directory to the simulation folder (the database and other files land there)
            # start_new_session=True creates a new process group, so os.killpg can stop every child process
            process = subprocess.Popen(
                cmd,
                cwd=sim_dir,
                stdout=main_log_file,
                stderr=subprocess.STDOUT,  # stderr goes to the same file
                text=True,
                encoding='utf-8',  # set the encoding explicitly
                bufsize=1,
                env=env,  # pass the environment carrying the UTF-8 settings
                start_new_session=True,  # create a new process group so every related process is stopped when the server exits
            )
            
            # Keep the file handles so they can be closed later
            cls._stdout_files[simulation_id] = main_log_file
            cls._stderr_files[simulation_id] = None  # a separate stderr file is no longer needed
            
            state.process_pid = process.pid
            state.runner_status = RunnerStatus.RUNNING
            cls._processes[simulation_id] = process
            cls._save_run_state(state)
            
            # Start the monitoring thread
            monitor_thread = threading.Thread(
                target=cls._monitor_simulation,
                args=(simulation_id,),
                daemon=True
            )
            monitor_thread.start()
            cls._monitor_threads[simulation_id] = monitor_thread
            
            logger.info(f"Simulation started: {simulation_id}, pid={process.pid}, platform={platform}")
            
        except Exception as e:
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)
            raise
        
        return state
    
    @classmethod
    def _monitor_simulation(cls, simulation_id: str):
        """Monitor the simulation process and parse the action logs"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        # The new log layout: per-platform action logs
        twitter_actions_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        reddit_actions_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        
        process = cls._processes.get(simulation_id)
        state = cls.get_run_state(simulation_id)
        
        if not process or not state:
            return
        
        twitter_position = 0
        reddit_position = 0
        
        try:
            while process.poll() is None:  # the process is still running
                # Read the Twitter action log
                if os.path.exists(twitter_actions_log):
                    twitter_position = cls._read_action_log(
                        twitter_actions_log, twitter_position, state, "twitter"
                    )
                
                # Read the Reddit action log
                if os.path.exists(reddit_actions_log):
                    reddit_position = cls._read_action_log(
                        reddit_actions_log, reddit_position, state, "reddit"
                    )
                
                # Update the state
                cls._save_run_state(state)
                time.sleep(2)
            
            # Once the process exits, read the logs one last time
            if os.path.exists(twitter_actions_log):
                cls._read_action_log(twitter_actions_log, twitter_position, state, "twitter")
            if os.path.exists(reddit_actions_log):
                cls._read_action_log(reddit_actions_log, reddit_position, state, "reddit")
            
            # The process has exited
            exit_code = process.returncode
            
            if exit_code == 0:
                state.runner_status = RunnerStatus.COMPLETED
                state.completed_at = datetime.now().isoformat()
                logger.info(f"Simulation complete: {simulation_id}")
            else:
                state.runner_status = RunnerStatus.FAILED
                # Read the error information from the main log file
                main_log_path = os.path.join(sim_dir, "simulation.log")
                error_info = ""
                try:
                    if os.path.exists(main_log_path):
                        with open(main_log_path, 'r', encoding='utf-8') as f:
                            error_info = f.read()[-2000:]  # take the last 2000 characters
                except Exception:
                    pass
                state.error = f"Process exit code: {exit_code}, error: {error_info}"
                logger.error(f"Simulation failed: {simulation_id}, error={state.error}")
            
            state.twitter_running = False
            state.reddit_running = False
            cls._save_run_state(state)
            
        except Exception as e:
            logger.error(f"Monitoring thread error: {simulation_id}, error={str(e)}")
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)
        
        finally:
            # Stop the graph memory updater
            if cls._graph_memory_enabled.get(simulation_id, False):
                try:
                    ZepGraphMemoryManager.stop_updater(simulation_id)
                    logger.info(f"Graph memory updates stopped: simulation_id={simulation_id}")
                except Exception as e:
                    logger.error(f"Failed to stop the graph memory updater: {e}")
                cls._graph_memory_enabled.pop(simulation_id, None)
            
            # Clean up the process resources
            cls._processes.pop(simulation_id, None)
            cls._action_queues.pop(simulation_id, None)
            
            # Close the log file handles
            if simulation_id in cls._stdout_files:
                try:
                    cls._stdout_files[simulation_id].close()
                except Exception:
                    pass
                cls._stdout_files.pop(simulation_id, None)
            if simulation_id in cls._stderr_files and cls._stderr_files[simulation_id]:
                try:
                    cls._stderr_files[simulation_id].close()
                except Exception:
                    pass
                cls._stderr_files.pop(simulation_id, None)
    
    @classmethod
    def _read_action_log(
        cls, 
        log_path: str, 
        position: int, 
        state: SimulationRunState,
        platform: str
    ) -> int:
        """
        Read an action log file
        
        Args:
            log_path: the log file path
            position: the previous read position
            state: the run state object
            platform: the platform name (twitter/reddit)
            
        Returns:
            the new read position
        """
        # Check whether graph memory updates are enabled
        graph_memory_enabled = cls._graph_memory_enabled.get(state.simulation_id, False)
        graph_updater = None
        if graph_memory_enabled:
            graph_updater = ZepGraphMemoryManager.get_updater(state.simulation_id)
        
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                f.seek(position)
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            action_data = json.loads(line)
                            
                            # Handle event-type entries
                            if "event_type" in action_data:
                                event_type = action_data.get("event_type")
                                
                                # A simulation_end event marks the platform as complete
                                if event_type == "simulation_end":
                                    if platform == "twitter":
                                        state.twitter_completed = True
                                        state.twitter_running = False
                                        logger.info(f"Twitter simulation complete: {state.simulation_id}, total_rounds={action_data.get('total_rounds')}, total_actions={action_data.get('total_actions')}")
                                    elif platform == "reddit":
                                        state.reddit_completed = True
                                        state.reddit_running = False
                                        logger.info(f"Reddit simulation complete: {state.simulation_id}, total_rounds={action_data.get('total_rounds')}, total_actions={action_data.get('total_actions')}")
                                    
                                    # Check whether every enabled platform has finished
                                    # When only one platform ran, check just that one
                                    # When two platforms ran, both must finish
                                    all_completed = cls._check_all_platforms_completed(state)
                                    if all_completed:
                                        state.runner_status = RunnerStatus.COMPLETED
                                        state.completed_at = datetime.now().isoformat()
                                        logger.info(f"All platform simulations complete: {state.simulation_id}")
                                
                                # Update the round information (from the round_end event)
                                elif event_type == "round_end":
                                    round_num = action_data.get("round", 0)
                                    simulated_hours = action_data.get("simulated_hours", 0)
                                    
                                    # Update the round and time of each platform separately
                                    if platform == "twitter":
                                        if round_num > state.twitter_current_round:
                                            state.twitter_current_round = round_num
                                        state.twitter_simulated_hours = simulated_hours
                                    elif platform == "reddit":
                                        if round_num > state.reddit_current_round:
                                            state.reddit_current_round = round_num
                                        state.reddit_simulated_hours = simulated_hours
                                    
                                    # The overall round is the maximum across both platforms
                                    if round_num > state.current_round:
                                        state.current_round = round_num
                                    # The overall time is the maximum across both platforms
                                    state.simulated_hours = max(state.twitter_simulated_hours, state.reddit_simulated_hours)
                                
                                continue
                            
                            action = AgentAction(
                                round_num=action_data.get("round", 0),
                                timestamp=action_data.get("timestamp", datetime.now().isoformat()),
                                platform=platform,
                                agent_id=action_data.get("agent_id", 0),
                                agent_name=action_data.get("agent_name", ""),
                                action_type=action_data.get("action_type", ""),
                                action_args=action_data.get("action_args", {}),
                                result=action_data.get("result"),
                                success=action_data.get("success", True),
                            )
                            state.add_action(action)
                            
                            # Update the round
                            if action.round_num and action.round_num > state.current_round:
                                state.current_round = action.round_num
                            
                            # When graph memory updates are enabled, push the activity to Zep
                            if graph_updater:
                                graph_updater.add_activity_from_dict(action_data, platform)
                            
                        except json.JSONDecodeError:
                            pass
                return f.tell()
        except Exception as e:
            logger.warning(f"Failed to read the action log: {log_path}, error={e}")
            return position
    
    @classmethod
    def _check_all_platforms_completed(cls, state: SimulationRunState) -> bool:
        """
        Check whether every enabled platform has finished its simulation
        
        A platform counts as enabled when its actions.jsonl file exists
        
        Returns:
            True when every enabled platform has finished
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, state.simulation_id)
        twitter_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        reddit_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        
        # Check which platforms are enabled (by the presence of their file)
        twitter_enabled = os.path.exists(twitter_log)
        reddit_enabled = os.path.exists(reddit_log)
        
        # Return False when an enabled platform has not finished
        if twitter_enabled and not state.twitter_completed:
            return False
        if reddit_enabled and not state.reddit_completed:
            return False
        
        # At least one enabled platform has finished
        return twitter_enabled or reddit_enabled
    
    @classmethod
    def _terminate_process(cls, process: subprocess.Popen, simulation_id: str, timeout: int = 10):
        """
        Terminate a process and its children across platforms
        
        Args:
            process: the process to terminate
            simulation_id: the simulation ID (for logging)
            timeout: how long to wait for the process to exit, in seconds
        """
        if IS_WINDOWS:
            # Windows: use taskkill to terminate the process tree
            # /F = force, /T = the whole process tree (children included)
            logger.info(f"Terminating the process tree (Windows): simulation={simulation_id}, pid={process.pid}")
            try:
                # Try a graceful termination first
                subprocess.run(
                    ['taskkill', '/PID', str(process.pid), '/T'],
                    capture_output=True,
                    timeout=5
                )
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    # Force the termination
                    logger.warning(f"The process did not respond, forcing termination: {simulation_id}")
                    subprocess.run(
                        ['taskkill', '/F', '/PID', str(process.pid), '/T'],
                        capture_output=True,
                        timeout=5
                    )
                    process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"taskkill failed, falling back to terminate: {e}")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        else:
            # Unix: terminate through the process group
            # Because start_new_session=True was used, the process group ID equals the main process PID
            pgid = os.getpgid(process.pid)
            logger.info(f"Terminating the process group (Unix): simulation={simulation_id}, pgid={pgid}")
            
            # Send SIGTERM to the whole process group first
            os.killpg(pgid, signal.SIGTERM)
            
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Force SIGKILL if it is still alive after the timeout
                logger.warning(f"The process group did not respond to SIGTERM, forcing termination: {simulation_id}")
                os.killpg(pgid, signal.SIGKILL)
                process.wait(timeout=5)
    
    @classmethod
    def stop_simulation(cls, simulation_id: str) -> SimulationRunState:
        """Stop the simulation"""
        state = cls.get_run_state(simulation_id)
        if not state:
            raise ValueError(f"Simulation not found: {simulation_id}")
        
        if state.runner_status not in [RunnerStatus.RUNNING, RunnerStatus.PAUSED]:
            raise ValueError(f"The simulation is not running: {simulation_id}, status={state.runner_status}")
        
        state.runner_status = RunnerStatus.STOPPING
        cls._save_run_state(state)
        
        # Terminate the process
        process = cls._processes.get(simulation_id)
        if process and process.poll() is None:
            try:
                cls._terminate_process(process, simulation_id)
            except ProcessLookupError:
                # The process is already gone
                pass
            except Exception as e:
                logger.error(f"Failed to terminate the process group: {simulation_id}, error={e}")
                # Fall back to terminating the process directly
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
        
        state.runner_status = RunnerStatus.STOPPED
        state.twitter_running = False
        state.reddit_running = False
        state.completed_at = datetime.now().isoformat()
        cls._save_run_state(state)
        
        # Stop the graph memory updater
        if cls._graph_memory_enabled.get(simulation_id, False):
            try:
                ZepGraphMemoryManager.stop_updater(simulation_id)
                logger.info(f"Graph memory updates stopped: simulation_id={simulation_id}")
            except Exception as e:
                logger.error(f"Failed to stop the graph memory updater: {e}")
            cls._graph_memory_enabled.pop(simulation_id, None)
        
        logger.info(f"Simulation stopped: {simulation_id}")
        return state
    
    @classmethod
    def _read_actions_from_file(
        cls,
        file_path: str,
        default_platform: Optional[str] = None,
        platform_filter: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        Read actions from a single action file
        
        Args:
            file_path: the action log file path
            default_platform: the default platform (used when a record has no platform field)
            platform_filter: filter by platform
            agent_id: filter by Agent ID
            round_num: filter by round
        """
        if not os.path.exists(file_path):
            return []
        
        actions = []
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                    
                    # Skip non-action records (simulation_start, round_start, round_end and other events)
                    if "event_type" in data:
                        continue
                    
                    # Skip records without an agent_id (not Agent actions)
                    if "agent_id" not in data:
                        continue
                    
                    # Get the platform: prefer the record's platform, otherwise the default
                    record_platform = data.get("platform") or default_platform or ""
                    
                    # Filter
                    if platform_filter and record_platform != platform_filter:
                        continue
                    if agent_id is not None and data.get("agent_id") != agent_id:
                        continue
                    if round_num is not None and data.get("round") != round_num:
                        continue
                    
                    actions.append(AgentAction(
                        round_num=data.get("round", 0),
                        timestamp=data.get("timestamp", ""),
                        platform=record_platform,
                        agent_id=data.get("agent_id", 0),
                        agent_name=data.get("agent_name", ""),
                        action_type=data.get("action_type", ""),
                        action_args=data.get("action_args", {}),
                        result=data.get("result"),
                        success=data.get("success", True),
                    ))
                    
                except json.JSONDecodeError:
                    continue
        
        return actions
    
    @classmethod
    def get_all_actions(
        cls,
        simulation_id: str,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        Fetch the complete action history across every platform (no paging)
        
        Args:
            simulation_id: the simulation ID
            platform: filter by platform (twitter/reddit)
            agent_id: filter by Agent
            round_num: filter by round
            
        Returns:
            the complete action list (sorted by timestamp, newest first)
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        actions = []
        
        # Read the Twitter action file (the platform is set to twitter automatically from the path)
        twitter_actions_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        if not platform or platform == "twitter":
            actions.extend(cls._read_actions_from_file(
                twitter_actions_log,
                default_platform="twitter",  # fills in the platform field automatically
                platform_filter=platform,
                agent_id=agent_id, 
                round_num=round_num
            ))
        
        # Read the Reddit action file (the platform is set to reddit automatically from the path)
        reddit_actions_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        if not platform or platform == "reddit":
            actions.extend(cls._read_actions_from_file(
                reddit_actions_log,
                default_platform="reddit",  # fills in the platform field automatically
                platform_filter=platform,
                agent_id=agent_id,
                round_num=round_num
            ))
        
        # When the per-platform file is missing, try the old single-file format
        if not actions:
            actions_log = os.path.join(sim_dir, "actions.jsonl")
            actions = cls._read_actions_from_file(
                actions_log,
                default_platform=None,  # the old format carries a platform field
                platform_filter=platform,
                agent_id=agent_id,
                round_num=round_num
            )
        
        # Sort by timestamp (newest first)
        actions.sort(key=lambda x: x.timestamp, reverse=True)
        
        return actions
    
    @classmethod
    def get_actions(
        cls,
        simulation_id: str,
        limit: int = 100,
        offset: int = 0,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        Fetch the action history (paged)
        
        Args:
            simulation_id: the simulation ID
            limit: how many to return
            offset: the offset
            platform: filter by platform
            agent_id: filter by Agent
            round_num: filter by round
            
        Returns:
            the action list
        """
        actions = cls.get_all_actions(
            simulation_id=simulation_id,
            platform=platform,
            agent_id=agent_id,
            round_num=round_num
        )
        
        # Paging
        return actions[offset:offset + limit]
    
    @classmethod
    def get_timeline(
        cls,
        simulation_id: str,
        start_round: int = 0,
        end_round: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch the simulation timeline (grouped by round)
        
        Args:
            simulation_id: the simulation ID
            start_round: the first round
            end_round: the last round
            
        Returns:
            the per-round summary information
        """
        actions = cls.get_actions(simulation_id, limit=10000)
        
        # Group by round
        rounds: Dict[int, Dict[str, Any]] = {}
        
        for action in actions:
            round_num = action.round_num
            
            if round_num < start_round:
                continue
            if end_round is not None and round_num > end_round:
                continue
            
            if round_num not in rounds:
                rounds[round_num] = {
                    "round_num": round_num,
                    "twitter_actions": 0,
                    "reddit_actions": 0,
                    "active_agents": set(),
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }
            
            r = rounds[round_num]
            
            if action.platform == "twitter":
                r["twitter_actions"] += 1
            else:
                r["reddit_actions"] += 1
            
            r["active_agents"].add(action.agent_id)
            r["action_types"][action.action_type] = r["action_types"].get(action.action_type, 0) + 1
            r["last_action_time"] = action.timestamp
        
        # Convert to a list
        result = []
        for round_num in sorted(rounds.keys()):
            r = rounds[round_num]
            result.append({
                "round_num": round_num,
                "twitter_actions": r["twitter_actions"],
                "reddit_actions": r["reddit_actions"],
                "total_actions": r["twitter_actions"] + r["reddit_actions"],
                "active_agents_count": len(r["active_agents"]),
                "active_agents": list(r["active_agents"]),
                "action_types": r["action_types"],
                "first_action_time": r["first_action_time"],
                "last_action_time": r["last_action_time"],
            })
        
        return result
    
    @classmethod
    def get_agent_stats(cls, simulation_id: str) -> List[Dict[str, Any]]:
        """
        Fetch per-Agent statistics
        
        Returns:
            the list of Agent statistics
        """
        actions = cls.get_actions(simulation_id, limit=10000)
        
        agent_stats: Dict[int, Dict[str, Any]] = {}
        
        for action in actions:
            agent_id = action.agent_id
            
            if agent_id not in agent_stats:
                agent_stats[agent_id] = {
                    "agent_id": agent_id,
                    "agent_name": action.agent_name,
                    "total_actions": 0,
                    "twitter_actions": 0,
                    "reddit_actions": 0,
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }
            
            stats = agent_stats[agent_id]
            stats["total_actions"] += 1
            
            if action.platform == "twitter":
                stats["twitter_actions"] += 1
            else:
                stats["reddit_actions"] += 1
            
            stats["action_types"][action.action_type] = stats["action_types"].get(action.action_type, 0) + 1
            stats["last_action_time"] = action.timestamp
        
        # Sort by total action count
        result = sorted(agent_stats.values(), key=lambda x: x["total_actions"], reverse=True)
        
        return result
    
    @classmethod
    def cleanup_simulation_logs(cls, simulation_id: str) -> Dict[str, Any]:
        """
        Clean up the run logs of a simulation (used to force a fresh restart)
        
        The following files are deleted:
        - run_state.json
        - twitter/actions.jsonl
        - reddit/actions.jsonl
        - simulation.log
        - stdout.log / stderr.log
        - twitter_simulation.db (the simulation database)
        - reddit_simulation.db (the simulation database)
        - env_status.json (the environment state)
        
        Note: the configuration (simulation_config.json) and the profile files are left alone
        
        Args:
            simulation_id: the simulation ID
            
        Returns:
            the cleanup result information
        """
        import shutil
        
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return {"success": True, "message": "The simulation folder does not exist, nothing to clean up"}
        
        cleaned_files = []
        errors = []
        
        # The files to delete (database files included)
        files_to_delete = [
            "run_state.json",
            "simulation.log",
            "stdout.log",
            "stderr.log",
            "twitter_simulation.db",  # the Twitter platform database
            "reddit_simulation.db",   # the Reddit platform database
            "env_status.json",        # the environment state file
        ]
        
        # The directories to delete (they hold the action logs)
        dirs_to_clean = ["twitter", "reddit"]
        
        # Delete the files
        for filename in files_to_delete:
            file_path = os.path.join(sim_dir, filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    cleaned_files.append(filename)
                except Exception as e:
                    errors.append(f"Failed to delete {filename}: {str(e)}")
        
        # Clean up the action logs in the platform folders
        for dir_name in dirs_to_clean:
            dir_path = os.path.join(sim_dir, dir_name)
            if os.path.exists(dir_path):
                actions_file = os.path.join(dir_path, "actions.jsonl")
                if os.path.exists(actions_file):
                    try:
                        os.remove(actions_file)
                        cleaned_files.append(f"{dir_name}/actions.jsonl")
                    except Exception as e:
                        errors.append(f"Failed to delete {dir_name}/actions.jsonl: {str(e)}")
        
        # Clear the in-memory run state
        if simulation_id in cls._run_states:
            del cls._run_states[simulation_id]
        
        logger.info(f"Simulation log cleanup complete: {simulation_id}, deleted files: {cleaned_files}")
        
        return {
            "success": len(errors) == 0,
            "cleaned_files": cleaned_files,
            "errors": errors if errors else None
        }
    
    # Flag that prevents repeated cleanup
    _cleanup_done = False
    
    @classmethod
    def cleanup_all_simulations(cls):
        """
        Clean up every running simulation process
        
        Called when the server shuts down, to make sure every child process is terminated
        """
        # Prevent repeated cleanup
        if cls._cleanup_done:
            return
        cls._cleanup_done = True
        
        # Check whether there is anything to clean up (avoids useless log output for an empty process table)
        has_processes = bool(cls._processes)
        has_updaters = bool(cls._graph_memory_enabled)
        
        if not has_processes and not has_updaters:
            return  # nothing to clean up, return silently
        
        logger.info("Cleaning up every simulation process...")
        
        # Stop every graph memory updater first (stop_all does its own logging)
        try:
            ZepGraphMemoryManager.stop_all()
        except Exception as e:
            logger.error(f"Failed to stop the graph memory updater: {e}")
        cls._graph_memory_enabled.clear()
        
        # Copy the dictionary so it can be iterated while being modified
        processes = list(cls._processes.items())
        
        for simulation_id, process in processes:
            try:
                if process.poll() is None:  # the process is still running
                    logger.info(f"Terminating the simulation process: {simulation_id}, pid={process.pid}")
                    
                    try:
                        # Use the cross-platform process termination helper
                        cls._terminate_process(process, simulation_id, timeout=5)
                    except (ProcessLookupError, OSError):
                        # The process may already be gone, so try terminating it directly
                        try:
                            process.terminate()
                            process.wait(timeout=3)
                        except Exception:
                            process.kill()
                    
                    # Update run_state.json
                    state = cls.get_run_state(simulation_id)
                    if state:
                        state.runner_status = RunnerStatus.STOPPED
                        state.twitter_running = False
                        state.reddit_running = False
                        state.completed_at = datetime.now().isoformat()
                        state.error = "The server shut down and the simulation was terminated"
                        cls._save_run_state(state)
                    
                    # Also update state.json, marking the status as stopped
                    try:
                        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
                        state_file = os.path.join(sim_dir, "state.json")
                        logger.info(f"Trying to update state.json: {state_file}")
                        if os.path.exists(state_file):
                            with open(state_file, 'r', encoding='utf-8') as f:
                                state_data = json.load(f)
                            state_data['status'] = 'stopped'
                            state_data['updated_at'] = datetime.now().isoformat()
                            with open(state_file, 'w', encoding='utf-8') as f:
                                json.dump(state_data, f, indent=2, ensure_ascii=False)
                            logger.info(f"state.json status updated to stopped: {simulation_id}")
                        else:
                            logger.warning(f"state.json does not exist: {state_file}")
                    except Exception as state_err:
                        logger.warning(f"Failed to update state.json: {simulation_id}, error={state_err}")
                        
            except Exception as e:
                logger.error(f"Failed to clean up the process: {simulation_id}, error={e}")
        
        # Clean up the file handles
        for simulation_id, file_handle in list(cls._stdout_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stdout_files.clear()
        
        for simulation_id, file_handle in list(cls._stderr_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stderr_files.clear()
        
        # Clear the in-memory state
        cls._processes.clear()
        cls._action_queues.clear()
        
        logger.info("Simulation process cleanup complete")
    
    @classmethod
    def register_cleanup(cls):
        """
        Register the cleanup function
        
        Called when the Flask app starts, so every simulation process is cleaned up when the server shuts down
        """
        global _cleanup_registered
        
        if _cleanup_registered:
            return
        
        # In Flask debug mode, register the cleanup only in the reloader child (the process actually running the app)
        # WERKZEUG_RUN_MAIN=true marks the reloader child
        # Outside debug mode that variable is absent, and registration is still needed
        is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
        is_debug_mode = os.environ.get('FLASK_DEBUG') == '1' or os.environ.get('WERKZEUG_RUN_MAIN') is not None
        
        # In debug mode, register only in the reloader child; outside debug mode, always register
        if is_debug_mode and not is_reloader_process:
            _cleanup_registered = True  # mark it as registered so a child process does not try again
            return
        
        # Keep the original signal handlers
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)
        # SIGHUP only exists on Unix (macOS/Linux), not on Windows
        original_sighup = None
        has_sighup = hasattr(signal, 'SIGHUP')
        if has_sighup:
            original_sighup = signal.getsignal(signal.SIGHUP)
        
        def cleanup_handler(signum=None, frame=None):
            """Signal handler: clean up the simulation processes first, then call the original handler"""
            # Only log when there are processes to clean up
            if cls._processes or cls._graph_memory_enabled:
                logger.info(f"Received signal {signum}, starting cleanup...")
            cls.cleanup_all_simulations()
            
            # Call the original signal handler so Flask exits normally
            if signum == signal.SIGINT and callable(original_sigint):
                original_sigint(signum, frame)
            elif signum == signal.SIGTERM and callable(original_sigterm):
                original_sigterm(signum, frame)
            elif has_sighup and signum == signal.SIGHUP:
                # SIGHUP: sent when the terminal closes
                if callable(original_sighup):
                    original_sighup(signum, frame)
                else:
                    # Default behaviour: exit normally
                    sys.exit(0)
            else:
                # When the original handler is not callable (e.g. SIG_DFL), use the default behaviour
                raise KeyboardInterrupt
        
        # Register the atexit handler (as a backstop)
        atexit.register(cls.cleanup_all_simulations)
        
        # Register the signal handlers (main thread only)
        try:
            # SIGTERM: the default signal sent by kill
            signal.signal(signal.SIGTERM, cleanup_handler)
            # SIGINT: Ctrl+C
            signal.signal(signal.SIGINT, cleanup_handler)
            # SIGHUP: terminal closed (Unix only)
            if has_sighup:
                signal.signal(signal.SIGHUP, cleanup_handler)
        except ValueError:
            # Not in the main thread, so atexit is the only option
            logger.warning("Cannot register signal handlers (not in the main thread); falling back to atexit")
        
        _cleanup_registered = True
    
    @classmethod
    def get_running_simulations(cls) -> List[str]:
        """
        Fetch the IDs of every running simulation
        """
        running = []
        for sim_id, process in cls._processes.items():
            if process.poll() is None:
                running.append(sim_id)
        return running
    
    # ============== Interview features ==============
    
    @classmethod
    def check_env_alive(cls, simulation_id: str) -> bool:
        """
        Check whether the simulation environment is alive (and can accept interview commands)

        Args:
            simulation_id: the simulation ID

        Returns:
            True means the environment is alive, False means it has been shut down
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            return False

        ipc_client = SimulationIPCClient(sim_dir)
        return ipc_client.check_env_alive()

    @classmethod
    def get_env_status_detail(cls, simulation_id: str) -> Dict[str, Any]:
        """
        Fetch the detailed state of the simulation environment

        Args:
            simulation_id: the simulation ID

        Returns:
            a state dictionary with status, twitter_available, reddit_available and timestamp
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        status_file = os.path.join(sim_dir, "env_status.json")
        
        default_status = {
            "status": "stopped",
            "twitter_available": False,
            "reddit_available": False,
            "timestamp": None
        }
        
        if not os.path.exists(status_file):
            return default_status
        
        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                status = json.load(f)
            return {
                "status": status.get("status", "stopped"),
                "twitter_available": status.get("twitter_available", False),
                "reddit_available": status.get("reddit_available", False),
                "timestamp": status.get("timestamp")
            }
        except (json.JSONDecodeError, OSError):
            return default_status

    @classmethod
    def interview_agent(
        cls,
        simulation_id: str,
        agent_id: int,
        prompt: str,
        platform: str = None,
        timeout: float = 60.0
    ) -> Dict[str, Any]:
        """
        Interview a single Agent

        Args:
            simulation_id: the simulation ID
            agent_id: Agent ID
            prompt: the interview question
            platform: a specific platform (optional)
                - "twitter": interview only on Twitter
                - "reddit": interview only on Reddit
                - None: on a dual-platform simulation, interview both platforms and return the combined result
            timeout: the timeout in seconds

        Returns:
            the interview result dictionary

        Raises:
            ValueError: the simulation does not exist or the environment is not running
            TimeoutError: the wait for the response timed out
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"Simulation not found: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)

        if not ipc_client.check_env_alive():
            raise ValueError(f"The simulation environment is not running or has been shut down, so the interview cannot run: {simulation_id}")

        logger.info(f"Sending the interview command: simulation_id={simulation_id}, agent_id={agent_id}, platform={platform}")

        response = ipc_client.send_interview(
            agent_id=agent_id,
            prompt=prompt,
            platform=platform,
            timeout=timeout
        )

        if response.status.value == "completed":
            return {
                "success": True,
                "agent_id": agent_id,
                "prompt": prompt,
                "result": response.result,
                "timestamp": response.timestamp
            }
        else:
            return {
                "success": False,
                "agent_id": agent_id,
                "prompt": prompt,
                "error": response.error,
                "timestamp": response.timestamp
            }
    
    @classmethod
    def interview_agents_batch(
        cls,
        simulation_id: str,
        interviews: List[Dict[str, Any]],
        platform: str = None,
        timeout: float = 120.0
    ) -> Dict[str, Any]:
        """
        Interview several Agents in one batch

        Args:
            simulation_id: the simulation ID
            interviews: the interview list, each item {"agent_id": int, "prompt": str, "platform": str (optional)}
            platform: the default platform (optional; overridden by each item's platform)
                - "twitter": interview only on Twitter by default
                - "reddit": interview only on Reddit by default
                - None: on a dual-platform simulation, interview each Agent on both platforms at once
            timeout: the timeout in seconds

        Returns:
            the batch interview result dictionary

        Raises:
            ValueError: the simulation does not exist or the environment is not running
            TimeoutError: the wait for the response timed out
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"Simulation not found: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)

        if not ipc_client.check_env_alive():
            raise ValueError(f"The simulation environment is not running or has been shut down, so the interview cannot run: {simulation_id}")

        logger.info(f"Sending the batch interview command: simulation_id={simulation_id}, count={len(interviews)}, platform={platform}")

        response = ipc_client.send_batch_interview(
            interviews=interviews,
            platform=platform,
            timeout=timeout
        )

        if response.status.value == "completed":
            return {
                "success": True,
                "interviews_count": len(interviews),
                "result": response.result,
                "timestamp": response.timestamp
            }
        else:
            return {
                "success": False,
                "interviews_count": len(interviews),
                "error": response.error,
                "timestamp": response.timestamp
            }
    
    @classmethod
    def interview_all_agents(
        cls,
        simulation_id: str,
        prompt: str,
        platform: str = None,
        timeout: float = 180.0
    ) -> Dict[str, Any]:
        """
        Interview every Agent (a global interview)

        Asks every Agent in the simulation the same question

        Args:
            simulation_id: the simulation ID
            prompt: the interview question (the same for every Agent)
            platform: a specific platform (optional)
                - "twitter": interview only on Twitter
                - "reddit": interview only on Reddit
                - None: on a dual-platform simulation, interview each Agent on both platforms at once
            timeout: the timeout in seconds

        Returns:
            the global interview result dictionary
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"Simulation not found: {simulation_id}")

        # Fetch every Agent's information from the configuration
        config_path = os.path.join(sim_dir, "simulation_config.json")
        if not os.path.exists(config_path):
            raise ValueError(f"The simulation configuration does not exist: {simulation_id}")

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        agent_configs = config.get("agent_configs", [])
        if not agent_configs:
            raise ValueError(f"The simulation configuration has no Agents: {simulation_id}")

        # Build the batch interview list
        interviews = []
        for agent_config in agent_configs:
            agent_id = agent_config.get("agent_id")
            if agent_id is not None:
                interviews.append({
                    "agent_id": agent_id,
                    "prompt": prompt
                })

        logger.info(f"Sending the global interview command: simulation_id={simulation_id}, agent_count={len(interviews)}, platform={platform}")

        return cls.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=interviews,
            platform=platform,
            timeout=timeout
        )
    
    @classmethod
    def close_simulation_env(
        cls,
        simulation_id: str,
        timeout: float = 30.0
    ) -> Dict[str, Any]:
        """
        Shut down the simulation environment (rather than stopping the simulation process)
        
        Sends a shutdown command so the simulation exits the command-wait mode gracefully
        
        Args:
            simulation_id: the simulation ID
            timeout: the timeout in seconds
            
        Returns:
            the result dictionary
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"Simulation not found: {simulation_id}")
        
        ipc_client = SimulationIPCClient(sim_dir)
        
        if not ipc_client.check_env_alive():
            return {
                "success": True,
                "message": "The environment is already shut down"
            }
        
        logger.info(f"Sending the environment shutdown command: simulation_id={simulation_id}")
        
        try:
            response = ipc_client.send_close_env(timeout=timeout)
            
            return {
                "success": response.status.value == "completed",
                "message": "The environment shutdown command has been sent",
                "result": response.result,
                "timestamp": response.timestamp
            }
        except TimeoutError:
            # The timeout may simply mean the environment is busy shutting down
            return {
                "success": True,
                "message": "The environment shutdown command has been sent (the wait timed out; the environment is probably shutting down)"
            }
    
    @classmethod
    def _get_interview_history_from_db(
        cls,
        db_path: str,
        platform_name: str,
        agent_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Read the interview history from a single database"""
        import sqlite3
        
        if not os.path.exists(db_path):
            return []
        
        results = []
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            if agent_id is not None:
                cursor.execute("""
                    SELECT user_id, info, created_at
                    FROM trace
                    WHERE action = 'interview' AND user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (agent_id, limit))
            else:
                cursor.execute("""
                    SELECT user_id, info, created_at
                    FROM trace
                    WHERE action = 'interview'
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
            
            for user_id, info_json, created_at in cursor.fetchall():
                try:
                    info = json.loads(info_json) if info_json else {}
                except json.JSONDecodeError:
                    info = {"raw": info_json}
                
                results.append({
                    "agent_id": user_id,
                    "response": info.get("response", info),
                    "prompt": info.get("prompt", ""),
                    "timestamp": created_at,
                    "platform": platform_name
                })
            
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to read the interview history ({platform_name}): {e}")
        
        return results

    @classmethod
    def get_interview_history(
        cls,
        simulation_id: str,
        platform: str = None,
        agent_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Fetch the interview history (read from the database)
        
        Args:
            simulation_id: the simulation ID
            platform: the platform type (reddit/twitter/None)
                - "reddit": only the Reddit history
                - "twitter": only the Twitter history
                - None: the history of both platforms
            agent_id: a specific Agent ID (optional; only that Agent's history)
            limit: how many to return per platform
            
        Returns:
            the list of interview history records
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        results = []
        
        # Work out which platforms to query
        if platform in ("reddit", "twitter"):
            platforms = [platform]
        else:
            # Query both platforms when no platform was given
            platforms = ["twitter", "reddit"]
        
        for p in platforms:
            db_path = os.path.join(sim_dir, f"{p}_simulation.db")
            platform_results = cls._get_interview_history_from_db(
                db_path=db_path,
                platform_name=p,
                agent_id=agent_id,
                limit=limit
            )
            results.extend(platform_results)
        
        # Sort by time, newest first
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        
        # When several platforms were queried, cap the total
        if len(platforms) > 1 and len(results) > limit:
            results = results[:limit]
        
        return results

