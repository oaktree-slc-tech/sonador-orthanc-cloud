import logging, orthanc, posixpath

from .web import StudyReviewerWorklistItemManagementView, StudyReviewerWorklistItemRestView


def init_reviewer_worklist(orthanc_conf, sonador_manager, OrthancSession):
	'''	Initialize Sonador reviewer worklist subsytem
	'''
	orthanc.RegisterRestCallback(r'/studies/([0-9a-fA-F]{8}\-?){5}/worklists', 
		StudyReviewerWorklistItemManagementView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))
	orthanc.RegisterRestCallback(r'/studies/([0-9a-fA-F]{8}\-?){5}/worklists/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', 
		StudyReviewerWorklistItemRestView.as_view(sonador_manager=sonador_manager, sessionmaker=OrthancSession))
