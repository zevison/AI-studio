/* Organize existing entry points without changing routes, drafts or permissions. */
(()=>{'use strict';
const groups=[['商品工作流',['products','radar','ads']],['创作工具',['video','hypit','image','assistant']],
              ['资料与设置',['assets','team-storage','history','settings']]];
const nav=document.querySelector('.sidebar nav');if(!nav)return;
const anchor=nav.querySelector('.nav-divider');
for(const [title,pages] of groups){
 const group=document.createElement('div');group.className='workflow-nav-group';group.setAttribute('role','group');group.setAttribute('aria-label',title);
 const label=document.createElement('div');label.className='workflow-nav-label';label.textContent=title;group.append(label);
 for(const page of pages){const button=nav.querySelector('.nav[data-page="'+page+'"]');if(button)group.append(button)}
 nav.insertBefore(group,anchor);
}
})();
