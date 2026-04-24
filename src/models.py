class Agent(Base):
    __tablename__ = "agents"
    id = Column(String, primary_key=True)
    type = Column(String)  # human | llm | script
    config = Column(JSON)

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String, primary_key=True)
    type = Column(String)  # direct | group

class Message(Base):
    __tablename__ = "messages"
    id = Column(String, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id"))
    sender_id = Column(String, ForeignKey("agents.id"))
    content = Column(Text)
    timestamp = Column(Float)