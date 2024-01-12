function OnDeletedPatient(patientId)
	-- Trigger Sonador internal REST endpoint to allow processing of "DELETE" patient events
	RestApiDelete('/sonador/internal/patient/change/' .. patientId)
end


function OnUpdatedPatient(patientId)
	-- Trigger Sonador internal REST endpoint to allow processing of "UPDATE" patient events
	RestApiPut('/sonador/internal/patient/change/' .. patientId, '{}')
end



function OnDeletedStudy(studyId)
	-- Trigger Sonador internal REST endpoint to allow processing of "DELETE" study events
	RestApiDelete('/sonador/internal/study/change/' .. studyId)
end 


function OnUpdatedStudy(studyId)
	-- Trigger Sonador internal REST endpoint to allow processing of "UPDATE" study events
	RestApiPut('/sonador/internal/study/change/' .. studyId, '{}')
end


function OnDeletedSeries(seriesId)
	-- Trigger Sonador internal REST endpoint to allow processing of "DELETE" series events	
	RestApiDelete('/sonador/internal/series/change/' .. seriesId)
end


function OnUpdatedSeries(seriesId)
	-- Trigger Sonador internal REST endpoint to allow processing of "UPDATE" series events
	RestApiPut('/sonador/internal/series/change/' .. seriesId, '{}')
end
