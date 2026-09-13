"""Domain logic, kept out of the routers.

A router's job is HTTP: parse, authorise, serialise. Anything a later milestone will
also need to do without a request — extracting a sub-video, storing an AI generation —
belongs here, where it can be called and tested directly.
"""
