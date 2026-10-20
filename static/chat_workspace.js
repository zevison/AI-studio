/* Presentation only: no provider requests, uploads or conversation mutations. */
(()=>{'use strict';
 const page=document.getElementById('page-assistant');if(!page)return;
 const toggle=document.getElementById('aiHistoryToggle'),panel=document.getElementById('aiHistoryPanel'),backdrop=document.getElementById('aiHistoryBackdrop');
 function history(open,restore=false){panel.hidden=!open;backdrop.hidden=!open;toggle.setAttribute('aria-expanded',String(open));if(restore)toggle.focus();}
 toggle.addEventListener('click',()=>history(panel.hidden));
 document.getElementById('aiHistoryClose').addEventListener('click',()=>history(false,true));
 backdrop.addEventListener('click',()=>history(false,true));
 panel.addEventListener('click',event=>{if(event.target.closest('[data-chat-id]'))history(false);});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!panel.hidden)history(false,true);});
 document.getElementById('aiNew').addEventListener('click',()=>history(false));
 const theme=document.getElementById('aiThemeToggle');
 function applyTheme(dark){page.dataset.chatTheme=dark?'dark':'light';theme.textContent=dark?'浅色':'深色';theme.setAttribute('aria-pressed',String(dark));}
 let saved='';try{saved=localStorage.getItem('seedance-chat-theme-v2')||'';}catch{}
 applyTheme(saved!=='light');
 theme.addEventListener('click',()=>{const dark=page.dataset.chatTheme!=='dark';applyTheme(dark);try{localStorage.setItem('seedance-chat-theme-v2',dark?'dark':'light');}catch{}});
})();
