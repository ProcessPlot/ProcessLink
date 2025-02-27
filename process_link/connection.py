# -*- coding: utf-8 -*-
#
# Copyright (c) 2022 Adam Solchenberger <asolchenberger@gmail.com>
# Copyright (c) 2022 Jason Engman <jengman@testtech-solutions.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#

from imp import acquire_lock
import threading, time
import re
from typing import Any, Optional
from .api import APIClass, PropertyError
from .tag import Tag
from .database import ConnectionDb
from .connections.connection_manager import ConxManager

__all__ = ["Connection", "TAG_TYPES", "UnknownConnectionError"]

TAG_TYPES = {'local': Tag}

class UnknownConnectionError(Exception):
    """
    raised when getting a connection that does not exist
    """

class Connection(APIClass):
    """
    The base connection class
    """

    def __repr__(self) -> str:
        return f"Connection: {self.id}"

    @property
    def id(self) -> str:
        return self._id

    @property
    def connection_type(self) -> str:
        return self._connection_type

    @property
    def description(self):
        return self._description
    @description.setter
    def description(self, value: str) -> None:
        self._description = value


    @property
    def tags(self):
        return self._tags

    @classmethod
    def get_params_from_db(cls, session, id: str):
        params = None
        orm = ConnectionDb.models["connection-params-local"]
        conn = session.query(orm).filter(orm.id == id).first()
        if conn:
            params = {
                'id': conn.id,
                'connection_type': conn.connection_type,
                'description': conn.description,
            }
        return params

    

    def __init__(self, process_link: "ProcessLink", params: dict) -> None:
        super().__init__()
        self.process_link = process_link
        self._tag_types = TAG_TYPES
        self.properties += ['id', 'connection_type', 'description', 'tags']
        try:
            params['id']
            params['connection_type']
        except KeyError as e:
            raise PropertyError(f"Missing expected property {e}")
        self._id = params.get('id')
        self._connection_type = "local" #base connection. Override this on exetended class' init to the correct type
        self._description = '' if 'description' not in params else params.get('description')
        self._tags = {}
        self._pollrate = 0.5 if 'pollrate' not in params else params.get('pollrate')
        self.base_orm = ConnectionDb.models["connection-params-local"] # database object-relational-model
        self.polled_tags = []
        self.thread_lock = False #thread lock used within the connection so polled_tags cannot change during polling
        #self.poll_thread = obj
        self.polling = False
        self.con_man = False
    
    def create_thread(self,*args):
        poll_thread = threading.Thread(target=self.poll)
        poll_thread.setDaemon(True)
        return poll_thread


    def set_polling(self, should_poll):
        if should_poll and not self.polling:
            poll_thread = self.create_thread()
            poll_thread.start()
        self.polling = should_poll

    def poll(self, *args):
        while(self.polling):
            ts = time.time()
            updates = {}
            while(self.thread_lock):
                time.sleep(0.001)
            self.thread_lock = True
            for tag in self.polled_tags:
                if not tag in updates:
                    updates[tag] = []
                updates[tag].append((3.14159, ts))
            self.process_link.update_handler.store_updates(updates)
            self.thread_lock = False
            time.sleep((ts+self._pollrate)-time.time())
    
    def new_tag(self, params) -> "Tag":
        """
        pass params for the properties of the tag. This will include
        the connection type and extended properties for that type
        return the Tag() 
        """
        params['connection_id'] = self.id
        try:
            self.tags[params["id"]] = TAG_TYPES[self.connection_type](params)
            return self.tags[params["id"]]
        except KeyError as e:
            raise PropertyError(f'Error creating tag, unknown type: {e}')
        
    def save_to_db(self, session: "db_session") -> str:
        entry = session.query(self.base_orm).filter(self.base_orm.id == self.id).first()
        if entry == None:
            entry = self.base_orm()
        entry.id = self.id
        entry.connection_type = self.connection_type
        entry.description = self.description
        session.add(entry)
        session.commit()
        if not self._id == entry.id:
            self._id = entry.id # if db created this, the widget has a new id
        return entry.id
########################New
    def delete_from_db(self,session: "db_session",conx_id):
        if conx_id != None:
            session.query(self.base_orm).filter(self.base_orm.id == conx_id).delete()
            session.commit()
########################New
    def load_tags_from_db(self, session):
        orm = ConnectionDb.models['tag-params-local']
        tags = session.query(orm).filter(orm.connection_id == self.id).all()
        for tag in tags:
            params = TAG_TYPES[self.connection_type].get_params_from_db(session, tag.id, self.id)
            self.new_tag(params)
########################New 
    def return_tag_parameters(self,*args):
        #default for local connection
        return ['id', 'connection_id', 'description','datatype','tag_type','value']
########################New 

    def aquire_lock(self) -> None:
        """
        used for safely updating and using data from multiple threads
        """
        while(self.thread_lock):
            time.sleep(0.001)
        self.thread_lock = True

    def update_polled_tags(self, sub_tags: list) -> None:
        self.aquire_lock()
        for tag in sub_tags:
            if tag not in self.polled_tags:
                self.polled_tags.append(tag)
        hitlist = []
        for i, tag in enumerate(self.polled_tags):
            if not tag in sub_tags:
                hitlist.append(i)
        for i in range(len(hitlist)-1, -1, -1): # iter in reverse so popping doesn't change index of the remaining tags
            self.polled_tags.pop(i)
        self.set_polling(bool(len(self.polled_tags)))
        self.thread_lock = False

#############################New
    def remove_polled_tags(self, sub_tags: list) -> None:
        ####Not sure this is going to work right
        self.aquire_lock()
        for tag in sub_tags:
            if tag in self.polled_tags:
                self.polled_tags.remove(tag)
        hitlist = []
        for i, tag in enumerate(self.polled_tags):
            if not tag in sub_tags:
                hitlist.append(i)
        for i in range(len(hitlist)-1, -1, -1): # iter in reverse so popping doesn't change index of the remaining tags
            self.polled_tags.pop(i)
        self.set_polling(bool(len(self.polled_tags)))
        self.thread_lock = False

    def connect_connection(self,conx_id,conx_params,tags):
        print('attemping connection')
        self.con_man = ConxManager(self.process_link)
        self.con_man.add_con(conx_params)
        for t in tags:
            self.con_man.new_tag(conx_id, tags[t])
        self.con_man.connect(conx_id)   #connection manager connect method attempt
        return self.con_man.is_polling(conx_id) #Returns whether connection was successful

    def disconnect_connection(self,conx_id,conx_params,tags):
        if self.con_man is not bool:
            print('disconnecting', conx_id)
            self.con_man.disconnect(conx_id)
            status = self.con_man.is_polling(conx_id) #Returns whether disconnection was successful
            self.con_man = False
            return status
        else:
            pass
#############################New



        

