from typing import Any, Dict, Optional
from dataclasses import dataclass, field
from datetime import date, timedelta, datetime

@dataclass
class Todo:
    text: str
    done: bool

    created_at: Optional[date] = field(default_factory=date.today)
    scheduled_at: Optional[date] = None
    completed_at: Optional[date] = None

    modified_at: Optional[datetime] = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'text': self.text,
            'done': self.done,
            'created_at': self.created_at.isoformat(),
            'scheduled_at': self.scheduled_at.isoformat() if self.scheduled_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'modified_at': self.modified_at.isoformat() if self.modified_at else None,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Todo":
        return Todo(
            text=data['text'],
            done=data['done'],
            created_at=date.fromisoformat(data['created_at'])
                if data.get('created_at') else date.today(),
            scheduled_at=date.fromisoformat(data['scheduled_at'])
                if data.get('scheduled_at') else None,
            completed_at=date.fromisoformat(data['completed_at'])
                if data.get('completed_at') else None,
            modified_at=datetime.fromisoformat(data['modified_at'])
                if data.get('modified_at') else datetime.now()
        )


def human_date(d: date) -> str:
    today = date.today()
    for i in range(7):
        day = today - timedelta(days=i)
        if day.weekday() == 0:
            monday = day
            break
    def is_current_week():
        diff = (d - monday).days
        return diff >= 0 and diff < 7

    def is_next_week():
        next_monday = monday + timedelta(days=7)
        diff = (d - next_monday).days
        return diff >= 0 and diff < 7

    def is_last_week():
        last_monday = monday - timedelta(days=7)
        diff = (d - last_monday).days
        return diff >= 0 and diff < 7

    weekday = d.strftime('%A')

    if d == today:
        return "Today"
    elif d == today - timedelta(days=1):
        return "Yesterday"
    elif d == today + timedelta(days=1):
        return "Tomorrow"
    elif is_current_week():
        return weekday 
    elif is_next_week():
        return f'Next {weekday}'
    elif is_last_week():
        return f'Last {weekday}'
    else:
        return d.strftime('%b %d, %Y')
