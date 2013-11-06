# -*- coding: utf-8 -*-
"""
Created on Fri Mar  1 16:14:42 2013

@author: proto
"""

#!/usr/bin/env python
from collections import OrderedDict
from scipy.misc import factorial, comb
import matplotlib.pyplot as plt
import libsbml
import bnglWriter as writer
from optparse import OptionParser
import moleculeCreation as mc
import sys
import structures
from os import listdir
import numpy as np
import analyzeRDF
from util import logMess
import re
import pickle
from copy import copy
log = {'species': [], 'reactions': []}
import signal
from collections import Counter

def handler(signum, frame):
    print "Forever is over!"
    raise Exception("end of time")


class SBML2BNGL:

    def __init__(self, model, useID=True):
        self.useID = useID
        self.model = model
        self.tags = {}
        self.boundaryConditionVariables = []
        self.speciesDictionary = {}
        self.speciesMemory = []
        self.getSpecies()
        
        self.reactionDictionary = {}
        
        
    def static_var(varname, value):
        def decorate(func):
            setattr(func, varname, value)
            return func
        return decorate

    def getRawSpecies(self, species,parameters=[]):
        '''
        *species* is the element whose SBML information we will extract
        this method gets information directly
        from an SBML related to a particular species.
        It returns id,initialConcentration,(bool)isconstant and isboundary,
        and the compartment
        It also accounts for the fact that sometimes ppl use the same name for 
        molecules with different identifiers
        '''
        identifier = species.getId()
        name = species.getName()
        if name == '':
            name = identifier
        initialConcentration = species.getInitialConcentration()
        if initialConcentration == 0:
            initialConcentration = species.getInitialAmount()
        
        isConstant = species.getConstant()
        isBoundary = species.getBoundaryCondition()
        if isBoundary:
            isConstant = True
        compartment = species.getCompartment()
        boundaryCondition = species.getBoundaryCondition()
        standardizedName = standardizeName(name)
        
        if standardizedName in parameters:
            standardizedName = 'sp_{0}'.format(standardizedName)
            
        #two speceis cannot have the same name. Ids are unique but less
        #informative
        if standardizedName in self.speciesMemory:
            standardizedName += '_' + species.getId()
        
        #it cannot start with a number
        if standardizedName[:1].isdigit():
            standardizedName = 's' + standardizedName
        
            
                        
        self.speciesMemory.append(standardizedName)
        if boundaryCondition:
            self.boundaryConditionVariables.append(standardizedName)
        self.speciesDictionary[identifier] = standardizedName
        returnID = identifier if self.useID else \
        self.speciesDictionary[identifier]
        return (returnID, initialConcentration, isConstant, isBoundary,
                compartment, name,identifier)


    '''
    walks through a series of * nodes and removes the remainder reactant factors
    '''
    def getPrunnedTree(self,math,remainderPatterns):
        while (math.getCharacter() == '*' or math.getCharacter() == '/') and len(remainderPatterns) > 0:
            if libsbml.formulaToString(math.getLeftChild()) in remainderPatterns:
                remainderPatterns.remove(libsbml.formulaToString(math.getLeftChild()))
                if math.getCharacter() == '*':
                    math = math.getRightChild()
                else:
                    math.getLeftChild().setValue(1)
            elif libsbml.formulaToString(math.getRightChild()) in remainderPatterns:
                remainderPatterns.remove(libsbml.formulaToString(math.getRightChild()))
                math = math.getLeftChild()            
            else:
                if(math.getLeftChild().getCharacter()) == '*':
                    math.replaceChild(0, self.getPrunnedTree(math.getLeftChild(), remainderPatterns))
                if(math.getRightChild().getCharacter()) == '*':
                    math.replaceChild(math.getNumChildren() - 1,self.getPrunnedTree(math.getRightChild(), remainderPatterns))
                break
        return math

    def getUnitDefinitions(self):
        for unitDefinition in self.model.getListOfUnits():
            pass
    def removeFactorFromMath(self, math, reactants, products):
        
            
        remainderPatterns = []
        highStoichoiMetryFactor = 1
        for x in reactants:
            highStoichoiMetryFactor  *= factorial(x[1])
            y = [i[1] for i in products if i[0] == x[0]]
            y = y[0] if len(y) > 0 else 0
            #TODO: check if this actually keeps the correct dynamics
            # this is basically there to address the case where theres more products
            #than reactants (synthesis)
            if x[1] > y:
                highStoichoiMetryFactor /= comb(int(x[1]), int(y), exact=True)
            for counter in range(0, int(x[1])):
                remainderPatterns.append(x[0])
        #for x in products:
        #    highStoichoiMetryFactor /= math.factorial(x[1])
        #remainderPatterns = [x[0] for x in reactants]
        math = self.getPrunnedTree(math,remainderPatterns)
        
        rateR = libsbml.formulaToString(math)
        for element in remainderPatterns:
            rateR = 'if({0}>0,({1})/{0},0)'.format(element,rateR)
        if highStoichoiMetryFactor != 1:
            rateR = '{0}*{1}'.format(rateR, int(highStoichoiMetryFactor))
        return rateR,math.getNumChildren()
        
    def __getRawRules(self, reaction):
        
        if self.useID:
            reactant = [(reactant.getSpecies(), reactant.getStoichiometry())
            for reactant in reaction.getListOfReactants() if
            reactant.getSpecies() != 'EmptySet']
            product = [(product.getSpecies(), product.getStoichiometry())
            for product in reaction.getListOfProducts() if product.getSpecies()
            != 'EmptySet']
        else:
            reactant = [(self.speciesDictionary[rElement.getSpecies()], rElement.getStoichiometry()) for rElement in reaction.getListOfReactants()]
            product = [(self.speciesDictionary[rProduct.getSpecies()], rProduct.getStoichiometry()) for rProduct in reaction.getListOfProducts()]
        kineticLaw = reaction.getKineticLaw()
        rReactant = [(x.getSpecies(), x.getStoichiometry()) for x in reaction.getListOfReactants() if x.getSpecies() != 'EmptySet']
        rProduct = [(x.getSpecies(), x.getStoichiometry()) for x in reaction.getListOfProducts() if x.getSpecies() != 'EmptySet']
        #rReactant = [reactant for reactant in reaction.getListOfReactants()]
        parameters = [(parameter.getId(), parameter.getValue()) for parameter in kineticLaw.getListOfParameters()]

        #TODO: For some reason creating a deepcopy of this screws everything up, even
        #though its what we should be doing
        math = kineticLaw.getMath()
        reversible = reaction.getReversible()
        
        #get a list of compartments so that we can remove them
        compartmentList  = []
        for compartment in (self.model.getListOfCompartments()):
            compartmentList.append(compartment.getId())
            
        #remove compartments from expression
        math = self.getPrunnedTree(math, compartmentList)
        if reversible:
            if math.getCharacter() == '-' and math.getNumChildren() > 1:
                rateL, nl = (self.removeFactorFromMath(
                math.getLeftChild().deepCopy(), rReactant, rProduct))
                rateR, nr = (self.removeFactorFromMath(
                math.getRightChild().deepCopy(), rProduct, rReactant))
            else:
                rateL, nl = self.removeFactorFromMath(math, rReactant,
                                                      rProduct)
                rateL = "if({0}>= 0,{0},0)".format(rateL)
                rateR, nr = self.removeFactorFromMath(math, rReactant,
                                                      rProduct)
                rateR = "if({0}< 0,-({0}),0)".format(rateR)
                nl, nr = 1,1
        else:
            rateL, nl = (self.removeFactorFromMath(math.deepCopy(),
                                                 rReactant,rProduct))
            rateR, nr = '0', '-1'
        if not self.useID:
            rateL = self.convertToName(rateL)
            rateR = self.convertToName(rateR)
        if reversible:
            pass

        #return compartments if the reaction is unimolecular
        #they were removed in the first palce because its easier to handle
        #around the equation in tree form when it has less terms
        '''
        if len(self.model.getListOfCompartments()) > 0:
            for compartment in (self.model.getListOfCompartments()):
                if compartment.getId() not in compartmentList:
                    if len(rReactant) != 2:
                        rateL = '{0} * {1}'.format(rateL,compartment.getSize())
                    if len(rProduct) != 2:
                         rateR = '{0} * {1}'.format(rateR,compartment.getSize())
        '''     


                
        return (reactant, product, parameters, [rateL, rateR],
                reversible, reaction.getId(), [nl, nr])
        
    def convertToName(self, rate):
        for element in sorted(self.speciesDictionary, key=len, reverse=True):
            if element in rate:
                rate = re.sub(r'(\W|^)({0})(\W|$)'.format(element),
                              r'\1{0}\3'.format(
                              self.speciesDictionary[element]), rate)
            #rate = rate.replace(element,self.speciesDictionary[element])
        return rate

    def __getRawCompartments(self, compartment):
        '''
        Private method used by the getCompartments method 
        '''
        name = compartment.getId()
        size = compartment.getSize()
        #if size != 1:
        #    print '!',
        return name,3,size
        
    def __getRawFunctions(self,function):
        math= function[1].getMath()
        name = function[1].getId()
        
        return name,libsbml.formulaToString(math)

    def getSBMLFunctions(self):
        functions = {}
        for function in enumerate(self.model.getListOfFunctionDefinitions()):
            functionInfo = self.__getRawFunctions(function)
            functions[functionInfo[0]] = (writer.bnglFunction(functionInfo[1],functionInfo[0],[],reactionDict=self.reactionDictionary))
        return functions
            
    def getCompartments(self):
        '''
        Returns an array of triples, where each triple is defined as
        (compartmentName,dimensions,size)
        '''
        compartments = []
        for _,compartment in enumerate(self.model.getListOfCompartments()):
            compartmentInfo = self.__getRawCompartments(compartment)
            name = 'cell' if compartmentInfo[0] == '' else compartmentInfo[0]
            compartments.append("%s  %d  %s" % (name, compartmentInfo[1], compartmentInfo[2]))
        return compartments

    def updateFunctionReference(self,reaction,updatedReferences):
        newRate = reaction[3]
        for reference in updatedReferences:
            newRate = re.sub(r'(\W|^)({0})(\W|$)'.format(reference),r'\1{0}\3'.format(updatedReferences[reference]),newRate)
            
        return newRate
    
    def getReactions(self, translator=[], isCompartments=False, extraParameters={}):
        '''
        returns a triple containing the parameters,rules,functions
        '''
        rules = []
        parameters = []
        
        functions = []
        
        functionTitle = 'functionRate'
        for index, reaction in enumerate(self.model.getListOfReactions()):
            parameterDict = {}
            rawRules =  self.__getRawRules(reaction)
            #newRate = self.updateFunctionReference(rawRules,extraParameters)
            if len(rawRules[2]) >0:
                for parameter in rawRules[2]:
                    parameters.append('r%d_%s %f' % (index+1, parameter[0], parameter[1]))
                    parameterDict[parameter[0]] = parameter[1]
            compartmentList = [['cell',1]]
            compartmentList.extend([[self.__getRawCompartments(x)[0],self.__getRawCompartments(x)[2]] for x in self.model.getListOfCompartments()])
            threshold = 0
            if rawRules[6][0] > threshold:  
                functionName = '%s%d()' % (functionTitle,index)
            else:
                #append reactionNumbers to parameterNames
                finalString = str(rawRules[3][0])
                for parameter in parameterDict:
                    finalString = re.sub(r'(\W|^)({0})(\W|$)'.format(parameter), r'\1{0}\3'.format('r{0}_{1}'.format(index+1,parameter)), finalString)
                functionName = finalString
            if 'delay' in rawRules[3][0]:
                logMess('ERROR','BNG cannot handle delay functions in function %s' % functionName)
            if rawRules[4]:
                if rawRules[6][0] > threshold:
                    functions.append(writer.bnglFunction(rawRules[3][0], functionName, rawRules[0], compartmentList, parameterDict, self.reactionDictionary))
                if rawRules[6][1] > threshold:
                    functionName2 = '%s%dm()' % (functionTitle,index)                
                    functions.append(writer.bnglFunction(rawRules[3][1],functionName2,rawRules[0],compartmentList,parameterDict,self.reactionDictionary))
                    self.reactionDictionary[rawRules[5]] = '({0} - {1})'.format(functionName, functionName2)                
                    functionName = '{0},{1}'.format(functionName, functionName2)
                else:
                    finalString = str(rawRules[3][1])
                    for parameter in parameterDict:
                        finalString = re.sub(r'(\W|^)({0})(\W|$)'.format(parameter),r'\1{0}\3'.format('r{0}_{1}'.format(index+1,parameter)),finalString)
                    functionName = '{0},{1}'.format(functionName,finalString)
            else:
                if rawRules[6][0] > threshold:
                    functions.append(writer.bnglFunction(rawRules[3][0], functionName, rawRules[0], compartmentList, parameterDict,self.reactionDictionary))
                    self.reactionDictionary[rawRules[5]] = '{0}'.format(functionName)
            #reactants = [x for x in rawRules[0] if x[0] not in self.boundaryConditionVariables]
            #products = [x for x in rawRules[1] if x[0] not in self.boundaryConditionVariables]
            reactants = [x for x in rawRules[0]]
            products = [x for x in rawRules[1]]
            rules.append(writer.bnglReaction(reactants,products,functionName,self.tags,translator,isCompartments,rawRules[4]))
        return parameters, rules,functions

    def __getRawAssignmentRules(self,arule):
        variable =   arule.getVariable()
        
        #try to separate into positive and negative sections
        if arule.getMath().getCharacter() == '-' and arule.getMath().getNumChildren() > 1 and not arule.isAssignment():
            rateL = libsbml.formulaToString(arule.getMath().getLeftChild())
            if(arule.getMath().getRightChild().getCharacter()) == '*':
                if libsbml.formulaToString(arule.getMath().getRightChild().getLeftChild()) == variable:
                    rateR = libsbml.formulaToString(arule.getMath().getRightChild().getRightChild())
                elif libsbml.formulaToString(arule.getMath().getRightChild().getRightChild()) == variable:
                    rateR = libsbml.formulaToString(arule.getMath().getRightChild().getLeftChild())
                else:
                    rateR = 'if({0}>0,({1})/{0},0)'.format(variable,libsbml.formulaToString(arule.getMath().getRightChild()))
            else:
                rateR = 'if({0}>0,({1})/{0},0)'.format(variable,libsbml.formulaToString((arule.getMath().getRightChild())))
        else:
            rateL = libsbml.formulaToString(arule.getMath())
            rateR = '0'
        if not self.useID:
            rateL = self.convertToName(rateL)
            rateR = self.convertToName(rateR)
            variable = self.convertToName(variable).strip()
        #print arule.isAssignment(),arule.isRate()
        return variable,[rateL, rateR], arule.isAssignment(), arule.isRate()
        
    def getAssignmentRules(self, zparams, parameters, molecules):
        '''
        this method obtains an SBML rate rules and assignment rules. They
        require special handling since rules are often both defined as rules 
        and parameters initialized as 0, so they need to be removed from the parameters list
        '''
        compartmentList = [['cell',1]]
        compartmentList.extend([[self.__getRawCompartments(x)[0], self.__getRawCompartments(x)[2]] for x in self.model.getListOfCompartments()])

        arules = []
        aParameters = {}
        zRules = zparams
        removeParameters = []
        artificialReactions = []
        artificialObservables = {}
        for arule in self.model.getListOfRules():
            
            rawArule = self.__getRawAssignmentRules(arule)
            #tmp.remove(rawArule[0])
            #newRule = rawArule[1].replace('+',',').strip()
            if rawArule[3] == True:
                #it is an rate rule
                if rawArule[0] in self.boundaryConditionVariables:
                    
                    aParameters[rawArule[0]] = 'arj' + rawArule[0] 
                    tmp = list(rawArule)
                    tmp[0] = 'arj' + rawArule[0]
                    rawArule = tmp


                rateLaw1 = rawArule[1][0]
                rateLaw2 = rawArule[1][1]
                arules.append(writer.bnglFunction(rateLaw1, 'arRate{0}'.format(rawArule[0]),[],compartments=compartmentList, reactionDict=self.reactionDictionary))
                arules.append(writer.bnglFunction(rateLaw2, 'armRate{0}'.format(rawArule[0]),[],compartments=compartmentList, reactionDict=self.reactionDictionary))
                artificialReactions.append(writer.bnglReaction([], [[rawArule[0],1]],'{0},{1}'.format('arRate{0}'.format(rawArule[0]), 'armRate{0}'.format(rawArule[0])), self.tags, {}, isCompartments=True, comment = '#rateLaw'))
                #arules.append(writer.bnglFunction('({0}) - ({1})'.format(rawArule[1][0],rawArule[1][1]), '{0}'.format(rawArule[0]),[],compartments=compartmentList, reactionDict=self.reactionDictionary))
                if rawArule[0] in zparams:
                    removeParameters.append('{0} 0'.format(rawArule[0]))
                    zRules.remove(rawArule[0])
                else:
                    for element in parameters:
                        #TODO: if for whatever reason a rate rule
                        #was defined as a parameter that is not 0
                        #remove it. This might not be exact behavior
                        logMess("WARNING","A name corresponds both as a non zero parameter \
                        and a rate rule, verify behavior")
                        if re.search('^{0}\s'.format(rawArule[0]), element):
                            removeParameters.append(element)
                        
            elif rawArule[2] == True:
                #it is an assigment rule

                if rawArule[0] in zRules:
                    zRules.remove(rawArule[0])

                if rawArule[0] in self.boundaryConditionVariables:
                    aParameters[rawArule[0]] = 'arj' + rawArule[0] 
                    tmp = list(rawArule)
                    tmp[0] = 'arj' + rawArule[0]
                    rawArule= tmp
    

                artificialObservables[rawArule[0]] = writer.bnglFunction(rawArule[1][0],rawArule[0]+'()',[],compartments=compartmentList,reactionDict=self.reactionDictionary)
            
            else:
                '''
                if for whatever reason you have a rule that is not assigment
                or rate and it is initialized as a non zero parameter, give it 
                a new name
                '''
                if rawArule[0] not in zparams:
                    ruleName = 'ar' + rawArule[0]
                else:
                    ruleName = rawArule[0]
                    zRules.remove(rawArule[0])
                arules.append(writer.bnglFunction(rawArule[1][0],ruleName,[],compartments=compartmentList,reactionDict=self.reactionDictionary))
                aParameters[rawArule[0]] = 'ar' + rawArule[0]
            '''
            elif rawArule[2] == True:
                for parameter in parameters:
                    if re.search('^{0}\s'.format(rawArule[0]),parameter):
                        print '////',rawArule[0]
            '''
            #arules.append('%s = %s' %(rawArule[0],newRule))
        return aParameters,arules,zRules,artificialReactions,removeParameters,artificialObservables

    def getParameters(self):
        parameters = []
        zparam = []
        for parameter in self.model.getListOfParameters():
            parameterSpecs = (parameter.getId(),parameter.getValue(),parameter.getConstant())
            #reserved keywords
            if parameterSpecs[0] == 'e':
                parameterSpecs = ('are',parameterSpecs[1])
            if parameterSpecs[1] == 0:
                zparam.append(parameterSpecs[0])
            else:
                parameters.append('{0} {1}'.format(parameterSpecs[0], parameterSpecs[1]))

        #return ['%s %f' %(parameter.getId(),parameter.getValue()) for parameter in self.model.getListOfParameters() if parameter.getValue() != 0], [x.getId() for x in self.model.getListOfParameters() if x.getValue() == 0]
        return parameters,zparam

    def getSpecies(self,translator = {},parameters = []):
        '''
        in sbml parameters and species have their own namespace. not so in
        bionetgen, so we need to rename things if they share the same name
        '''

        moleculesText  = []
        speciesText = []
        observablesText = []
        names = []
        rawSpeciesName = translator.keys()
        
        compartmentDict = {}
        compartmentDict[''] = 1
        for compartment in self.model.getListOfCompartments():
            compartmentDict[compartment.getId()] = compartment.getSize()

        for species in self.model.getListOfSpecies():
            rawSpecies = self.getRawSpecies(species,parameters)
            #if rawSpecies[0] in self.boundaryConditionVariables:
            #    continue
            if (rawSpecies[4] != ''):
                self.tags[rawSpecies[0]] = '@%s' % (rawSpecies[4])
            if(rawSpecies[0] in translator):
                if rawSpecies[0] in rawSpeciesName:
                    rawSpeciesName.remove(rawSpecies[0])
                if translator[rawSpecies[0]].getSize()==1 and translator[rawSpecies[0]].molecules[0].name not in names:
                    names.append(translator[rawSpecies[0]].molecules[0].name)
                    moleculesText.append(translator[rawSpecies[0]].str2())
            else:
                moleculesText.append(rawSpecies[0] + '()')
            temp = '$' if rawSpecies[2] != 0 else ''
            tmp = translator[str(rawSpecies[0])] if rawSpecies[0] in translator \
                else rawSpecies[0] + '()'
            if rawSpecies[1]>=0:
                #tmp= translator[rawSpecies[0]].toString()
                #print translator[rawSpecies[0]].toString()
                tmp2 = temp
                if rawSpecies[0] in self.tags:
                    tmp2 = (self.tags[rawSpecies[0]])
                if rawSpecies[1] > 0.0:
                    #if compartmentDict[rawSpecies[4]] != 1.0:
                    #    speciesText.append('{0}:{1}{2} {3}/{4}'.format(tmp2, temp, str(tmp), rawSpecies[1],compartmentDict[rawSpecies[4]]))
                    #else:
                    speciesText.append('{0}:{1}{2} {3}'.format(tmp2, temp, str(tmp), rawSpecies[1]))
            if rawSpecies[0] == 'e':
                modifiedName = 'are'
            else:
                modifiedName = rawSpecies[0]
            observablesText.append('Species {0} {1} #{2}'.format(modifiedName, tmp,rawSpecies[5]))
        sorted(rawSpeciesName,key=len)
        for species in rawSpeciesName:
            if translator[species].getSize()==1 and translator[species].molecules[0].name not in names:
                names.append(translator[species].molecules[0].name)
                moleculesText.append(translator[species].str2())
        #moleculesText.append('NullSpecies()')
        #speciesText.append('$NullSpecies() 1')
        self.speciesMemory = []
        return moleculesText,speciesText,observablesText

    def getSpeciesAnnotation(self):
        speciesAnnotation = {}

        for species in self.model.getListOfSpecies():
            rawSpecies = self.getRawSpecies(species)
            annotationXML = species.getAnnotation()
            lista = libsbml.CVTermList()
            libsbml.RDFAnnotationParser.parseRDFAnnotation(annotationXML,lista)
            if lista.getSize() == 0:
                speciesAnnotation[rawSpecies[0]] =  None
            else:
                speciesAnnotation[rawSpecies[0]] = lista.get(0).getResources()
        return speciesAnnotation

    def getModelAnnotation(self):
        modelAnnotation = []
        annotationXML = self.model.getAnnotation()
        lista = libsbml.CVTermList()
        libsbml.RDFAnnotationParser.parseRDFAnnotation(annotationXML,lista)
        if lista.getSize() == 0:
            modelAnnotations = []
        else:
            tempDict = {}
            for element in [2,3,4,5,6]:
                if lista.get(element) == None:
                    continue
                tempDict[lista.get(element).getBiologicalQualifierType()] = lista.get(element)
            if 3 in tempDict:
                modelAnnotation = tempDict[3].getResources()
            elif 0 in tempDict and ('GO' in tempDict[0].getResources().getValue(1) or 'kegg' in tempDict[0].getResources().getValue(1)):
                modelAnnotation = tempDict[0].getResources()
            elif 5 in tempDict:
                modelAnnotation = tempDict[5].getResources()
            else:
                if lista.get(3) != None and ('GO' in lista.get(3).getResources().getValue(0) or 'kegg' in lista.get(3).getResources().getValue(0)):
                    modelAnnotation = lista.get(3).getResources()
                    
                elif lista.get(4) != None and ('GO' in lista.get(4).getResources().getValue(0) or 'kegg' in lista.get(4).getResources().getValue(0)):
                    modelAnnotation = lista.get(4).getResources()
                elif lista.get(5) != None and ('GO' in lista.get(5).getResources().getValue(0) or 'kegg' in lista.get(5).getResources().getValue(0)):
                    modelAnnotation = lista.get(5).getResources()
                else:
                    if lista.get(3) != None and ('reactome' in lista.get(3).getResources().getValue(0)):
                        modelAnnotation = lista.get(3).getResources()
                        
                    elif lista.get(4) != None and ('reactome' in lista.get(4).getResources().getValue(0)):
                        modelAnnotation = lista.get(4).getResources()
                    elif lista.get(5) != None and ('reactome' in lista.get(5).getResources().getValue(0)):
                        modelAnnotation = lista.get(5).getResources()
                    elif lista.get(2) != None:
                        modelAnnotation = lista.get(2).getResources()
        return modelAnnotation
        
    def getSpeciesInfo(self, name):
        return self.getRawSpecies(self.model.getSpecies(name))

    def writeLog(self, translator):
        rawSpecies = [self.getRawSpecies(x) for x in self.model.getListOfSpecies()]
        log['species'].extend([x[0] for x in rawSpecies if x[0] not in translator])
        logString = ''
        #species stuff
        if(len(log['species']) > 0):
            logString += "Species we couldn't recognize:\n"
            for element in log['species']:
                logString += '\t%s\n' % element
        if(len(log['reactions']) > 0):
            logString += "Reactions we couldn't infer more about due to \
            insufficient information:"
            for element in log['reactions']:
                logString += '\t%s + %s -> %s\n' % (element[0][0],
                                                    element[0][1],
                                                    element[1])
        return logString

    def getStandardName(self,name):
        if name in self.speciesDictionary:
            return self.speciesDictionary[name]
        return name
        
def standardizeName(name):
    name2 = name
    
    
    name2 = name.replace("-","_")
    name2 = name2.replace("^","")
    name2 = name2.replace("'","")
    name2 = name2.replace("*","m")
    #name2 = name2.replace("#","m")
    name2 = name2.replace(" ","_")
    name2 = name2.replace(",","_")
    name2 = name2.replace('α','a')
    name2 = name2.replace('β','b')
    name2 = name2.replace('γ','g')
    name2 = name2.replace("(","")
    name2 = name2.replace(")","")
    name2 = name2.replace(" ","")
    name2 = name2.replace("+","pl")
    name2 = name2.replace("/","_")
    name2 = name2.replace(":","_")
    name2 = name2.replace(".","_")
    
    
    return name2
        


def identifyNamingConvention():
    '''
    extracts statistics from the code
    '''
    
    reader = libsbml.SBMLReader()
    jsonFiles = [ f for f in listdir('./reactionDefinitions') if f[-4:-1] == 'jso' and 'reactionDefinition' in f]
    translationLevel = []
    arrayMolecules = []
    rules = 0
    #go through all curated models in the biomodels database
    signal.signal(signal.SIGALRM, handler)
    for index in range(1,410):
        bestTranslator = {}
        
        nameStr = 'BIOMD0000000%03d' % (index)
        document = reader.readSBMLFromFile('XMLExamples/curated/' + nameStr + '.xml')
        parser = SBML2BNGL(document.getModel())
        database = structures.Databases()

        print nameStr + '.xml',
        naming = 'reactionDefinition0.json'
        bestUseID = True
        numberofMolecules = numberOfSpecies = 0
        #iterate through our naming conventions and selects that which
        #creates the most rulified elements in the translator
        for jsonFile in jsonFiles:
            for useID in [True,False]:
                oldmaxi = numberOfSpecies
                parser = SBML2BNGL(document.getModel(),useID)
                database = structures.Databases()
                signal.alarm(30)
                try:
                    mc.transformMolecules(parser,database,'reactionDefinitions/' + jsonFile,None)
                except:
                    print '--error',jsonFile,useID
                    continue
                translator = database.translator                        
                numberOfSpecies = max(numberOfSpecies,evaluation(len(parser.getSpecies()),database.translator))
                if oldmaxi != numberOfSpecies:
                    naming = jsonFile
                    bestTranslator = translator
                    bestUseID = useID
                    _,rules,_ = parser.getReactions(translator)
                    numberofMolecules = len(translator)

        _,_,obs = parser.getSpecies()
        rdfAnnotations = analyzeRDF.getAnnotations(parser,'miriam')
        #go through the annotation list and assign which species
        #correspond to which uniprot number (if it exists)
        #similarly list the number of times each individual element appears
        analyzeRDF.getAnnotations(parser,'miriam')
        molecules = {}
        if naming[-6] != 0:
            for element in bestTranslator:
                if len(bestTranslator[element].molecules) == 1:
                    name = bestTranslator[element].molecules[0].name
                    for annotation in rdfAnnotations:
                        if name in rdfAnnotations[annotation]:
                            if name not in molecules:
                                molecules[name] = [0,[]]
                            if annotation not in molecules[name][1]:
                                molecules[name][1].extend(annotation)
                    
                    if  name not in molecules:
                        molecules[name] = [1,[]]
                    for rule in rules:
                        if name in rule:
                            molecules[name][0] += 1
        
       # _,rules,_ = parser.getReactions(bestTranslator)
       #for rule in rules:
        
        if len(obs) != 0:
            print index*1.0,(naming[-6]),numberOfSpecies*1.0/len(obs),numberofMolecules*1.0/len(obs),len(obs)*1.0,bestUseID
            
            arrayMolecule = [[x,molecules[x]] for x in molecules]
            arrayMolecules.append(arrayMolecule)
            translationLevel.append([nameStr+'.xml',(naming[-6]),numberOfSpecies*1.0/len(obs),numberofMolecules*1.0/len(obs),len(obs)*1.0,bestUseID])
        else:
            arrayMolecules.append([])
    with open('stats4.npy','wb') as f:
        pickle.dump(translationLevel,f)

        #np.save('stats3b.npy',np.array(arrayMolecules))

def processDatabase():
    reader = libsbml.SBMLReader()
    #jsonFiles = [ f for f in listdir('./reactionDefinitions') if f[-4:-1] == 'jso']
    history = np.load('stats3.npy')
    index2 = 0
    for index in range(1,410):
        try:
            nameStr = 'BIOMD0000000%03d' % (index)
            document = reader.readSBMLFromFile('XMLExamples/curated/' + nameStr + '.xml')
            parser = SBML2BNGL(document.getModel())
            database = structures.Databases()

            print nameStr + '.xml'
            '''
            for jsonFile in jsonFiles:
                try:
                    #print jsonFile,
                    translator = m2c.transformMolecules(parser,database,'reactionDefinitions/' + jsonFile)
                    break
                except:
                    print 'ERROR',sys.exc_info()[0]
                    continue
                #translator = m2c.transformMolecules(parser,database,'reactionDefinition2.json')
            '''
            #translator = []
            while(history[index2][0] < index):
                index2=1
            print history[index2][0],index
            if (history[index2][0]==index) and history[index2][1] != 0:
                print str( int(history[index2][1]))
                translator = mc.transformMolecules(parser,database,'reactionDefinitions/reactionDefinition' + str( int(history[index2][1])) + '.json')            
            else:
                translator = {}
            #print len(parser.getSpecies()),len(translator),
            evaluation(len(parser.getSpecies()),translator)

            #translator = {}
            param2 = parser.getParameters()
            molecules,species,observables = parser.getSpecies(translator)
            #print molecules,species,observables
            print 'translated: {0}/{1}'.format(len(translator),len(observables)),
            print evaluation(len(observables),translator)
            param,rules,functions = parser.getReactions(translator)
            compartments = parser.getCompartments()
            param += param2
            writer.finalText(param,molecules,species,observables,rules,functions,compartments,'output/' + nameStr + '.bngl')
            with open('output/' + nameStr + '.log', 'w') as f:
                f.write(parser.writeLog(translator))
        except:
            print 'ERROR',sys.exc_info()[0]
            continue

def evaluation(numMolecules,translator):
    originalElements = (numMolecules)
    nonStructuredElements = len([1 for x in translator if '()' in str(translator[x])])
    ruleElements = (len(translator) - nonStructuredElements)*1.0/originalElements
    return ruleElements


    #print rules
#14,18,56,19,49.87.88.107,109,111,120,139,140,145,151,153,171,175,182,202,205
#230,253,255,256,268,269,288,313,332,333,334,335,336,362,396,397,399,406


def selectReactionDefinitions(bioNumber):
    '''
    This method goes through the stats-biomodels database looking for the 
    best reactionDefinitions definition available
    '''
    with open('stats4.npy') as f:
        db = pickle.load(f)
    fileName = 'reactionDefinitions/reactionDefinition7.json'
    useID = True
    for element in db:    
        if element[0] == bioNumber and element[1] != '0':
            fileName = 'reactionDefinitions/reactionDefinition' + element[1] + '.json'
            useID = element[5]
        elif element[0] > bioNumber:
            break
    return fileName,useID


def resolveDependencies(dictionary,key,idx):
    counter = 0
    for element in dictionary[key]:
        if idx < 20:
            counter += resolveDependencies(dictionary,element,idx+1)
        else:
            counter += 1
    return len(dictionary[key]) + counter    
    
def validateReactionUsage(reactant,reactions):
    for element in reactions:
        if reactant in element:
            return element
    return None


def readFromString(inputString,reactionDefinitions,useID,speciesEquivalence=None,atomize=False):
    '''
    one of the library's main entry methods. Process data from a string
    '''
    reader = libsbml.SBMLReader()
    document = reader.readSBMLFromString(inputString)
    return analyzeHelper(document,reactionDefinitions,useID,'',speciesEquivalence,atomize)[-1]

def processFunctions(functions,sbmlfunctions,artificialObservables,tfunc):
    '''
    this method goes through the list of functions and removes all
    sbml elements that are extraneous to bngl
    '''
    
    for idx in range(0,len(functions)):
        for sbml in sbmlfunctions:
            if sbml in functions[idx]:
                functions[idx] = writer.extendFunction(functions[idx],sbml,sbmlfunctions[sbml])
        functions[idx] =re.sub(r'(\W|^)(time)(\W|$)',r'\1time()\3',functions[idx])
        functions[idx] =re.sub(r'(\W|^)(Time)(\W|$)',r'\1time()\3',functions[idx])
        functions[idx] =re.sub(r'(\W|^)(t)(\W|$)',r'\1time()\3',functions[idx])
    #functions.extend(sbmlfunctions)
    dependencies2 = {}
    for idx in range(0,len(functions)):
        dependencies2[functions[idx].split(' = ')[0].split('(')[0].strip()] = []
        for key in artificialObservables:
            oldfunc = functions[idx]
            functions[idx] = (re.sub(r'(\W|^)({0})([^\w(]|$)'.format(key),r'\1\2()\3',functions[idx]))
            if oldfunc != functions[idx]:
                dependencies2[functions[idx].split(' = ')[0].split('(')[0]].append(key)
        for element in sbmlfunctions:
            oldfunc = functions[idx]
            key = element.split(' = ')[0].split('(')[0]
            if re.search('(\W|^){0}(\W|$)'.format(key),functions[idx].split(' = ')[1]) != None:
                dependencies2[functions[idx].split(' = ')[0].split('(')[0]].append(key)
        for element in tfunc:
            key = element.split(' = ')[0].split('(')[0]
            if key in functions[idx].split(' = ')[1]:
                dependencies2[functions[idx].split( ' = ')[0].split('(')[0]].append(key)
    '''           
    for counter in range(0,3):
        for element in dependencies2:
            if len(dependencies2[element]) > counter:
                dependencies2[element].extend(dependencies2[dependencies2[element][counter]])
    '''
    fd = []
    for function in functions:
        fd.append([function,resolveDependencies(dependencies2,function.split(' = ' )[0].split('(')[0],0)])
    fd = sorted(fd,key= lambda rule:rule[1])
    functions = [x[0] for x in fd]
    return functions


def extractAtoms(species):
    '''
    given a list of structures, returns a list
    of individual molecules/compartment pairs
    appends a number for 
    '''
    listOfAtoms = set()
    for molecule in species.molecules:
        for component in molecule.components:
            listOfAtoms.add(tuple([molecule.name,component.name]))
    return listOfAtoms


def bondPartners(species,bondNumber):
    relevantComponents = []
    for molecule in species.molecules:
        for component in molecule.components:
            if bondNumber in component.bonds:
                relevantComponents.append(tuple([molecule.name,component.name]))
    return relevantComponents
    
def getMoleculeByName(species,atom):
    '''
    returns the state of molecule-component contained in atom
    '''
    
    stateVectorVector = []
    for molecule in species.molecules:
        if molecule.name == atom[0]:
            stateVector = []
            for component in molecule.components:
                if component.name == atom[1]:
                    
                    #get whatever species this atom is bound to
                    if len(component.bonds) > 0:
                        comp = bondPartners(species,component.bonds[0])
                        comp.remove(atom)
                        if len(comp) > 0:
                            stateVector.append(comp[0])
                        else:
                            stateVector.append('')
                    else:
                        stateVector.append('')
                    if len(component.states) > 0:
                        stateVector.append(component.activeState)
                    else:
                        stateVector.append('')
            stateVectorVector.append(stateVector)
    return tuple(stateVectorVector[0])
        
    
    
def extractCompartmentCoIncidence(species):
    atomPairDictionary = {}
    if [x.name for x in species.molecules] == ['EGF','EGF','EGFR','EGFR']:
        pass
    for molecule in species.molecules:
        for component in molecule.components:
            for component2 in molecule.components:
                if component == component2:
                    continue
                atom = tuple([molecule.name,component.name])
                atom2 = tuple([molecule.name,component2.name])
                molId1 = getMoleculeByName(species,atom)
                molId2 = getMoleculeByName(species,atom2)
                key = tuple([atom,atom2])
                #print key,(molId1,molId2)
                if key not in atomPairDictionary:
                    atomPairDictionary[key] = Counter()
                atomPairDictionary[key].update([tuple([molId1,molId2])])

    return atomPairDictionary    
    
def extractCompartmentStatistics(bioNumber,useID,reactionDefinitions,speciesEquivalence):
    '''
    Iterate over the translated species and check which compartments
    are used together, and how. 
    '''
    reader = libsbml.SBMLReader()
    document = reader.readSBMLFromFile(bioNumber)
    
    
    parser =SBML2BNGL(document.getModel(),useID)
    database = structures.Databases()
    
    #call the atomizer (or not)
    #if atomize:
    translator = mc.transformMolecules(parser,database,reactionDefinitions,speciesEquivalence)
    #else:    
    #    translator={} 


    compartmentPairs = {}
    for element in translator:
        temp = extractCompartmentCoIncidence(translator[element])
        for element in temp:
            if element not in compartmentPairs:
                compartmentPairs[element] = temp[element]
            else:
                compartmentPairs[element].update(temp[element])
    finalCompartmentPairs = {}
    print '-----'
    for element in compartmentPairs:
        if element[0][0] not in finalCompartmentPairs:
            finalCompartmentPairs[element[0][0]] = {}
        finalCompartmentPairs[element[0][0]][tuple([element[0][1],element[1][1]])] = compartmentPairs[element]
    return finalCompartmentPairs
    
def recursiveSearch(dictionary,element):
    tmp = 0
    for item in dictionary[element]:
        if dictionary[item] == []:
            tmp +=1
        else:
            tmp += 1
            tmp += (recursiveSearch(dictionary,item))
    return tmp

def reorderFunctions(functions):
    ''''
    Analyze a list of sbml functions and make sure there are no forward dependencies. 
    Reorder if necessary
    '''    
    functionNames = []
    tmp = []
    for function in functions:
        m = re.split('(?<=\()[\w)]', function)
        functionNames.append(m[0])
    functionNamesDict = {x:[] for x in functionNames}
    for idx,function in enumerate(functions):
        tmp = [x for x in functionNames if x in function and x!= functionNames[idx]]
        functionNamesDict[functionNames[idx]].extend(tmp)
    newFunctionNamesDict = {}
    for name in functionNamesDict:
        newFunctionNamesDict[name] = recursiveSearch(functionNamesDict,name)
    functionWeightsDict = {x:newFunctionNamesDict[x] for x in newFunctionNamesDict}
    functionWeights = []
    for name in functionNames:
        functionWeights.append(functionWeightsDict[name])
    tmp = zip(functions,functionWeights)
    idx = sorted(tmp,key=lambda x:x[1])
    return [x[0] for x in idx]
    
    
def analyzeFile(bioNumber,reactionDefinitions,useID,outputFile,speciesEquivalence=None,atomize=False):
    '''
    one of the library's main entry methods. Process data from a string
    '''
    reader = libsbml.SBMLReader()
    document = reader.readSBMLFromFile(bioNumber)
    
    parser =SBML2BNGL(document.getModel(),useID)
    database = structures.Databases()
    
    #call the atomizer (or not)
    if atomize:
        translator = mc.transformMolecules(parser,database,reactionDefinitions,speciesEquivalence)
    else:    
        translator={} 

    
    returnArray= analyzeHelper(document,reactionDefinitions,useID,outputFile,speciesEquivalence,atomize,translator)
    with open(outputFile,'w') as f:
            f.write(returnArray[-1])
    return returnArray[0:-1]

def correctRulesWithParenthesis(rules,parameters):
    '''
    helper function. Goes through a list of rules and adds a parenthesis
    to the reaction rates of those functions whose rate is in list 
    'parameters'
    '''
    for idx in range(len(rules)):
        tmp = [x for x in parameters if x + ' ' in rules[idx]]
        if len(tmp) > 0:
            rules[idx].strip()
            rules[idx] += '()'
    
def changeNames(functions,dictionary):
    '''
    changes instances of keys in dictionary appeareing in functions to their corresponding
    alternatives
    '''
    tmpArray = []
    for function in functions:
        tmp = function.split(' = ')
        for key in [x for x in dictionary if x in tmp[1]]:
            tmp[1] = re.sub(r'(\W|^){0}(\W|$)'.format(key),r'\1{0}\2'.format(dictionary[key]),tmp[1])
        tmpArray.append('{0} = {1}'.format(tmp[0],tmp[1]))
    return tmpArray
    
def changeRates(reactions,dictionary):
    tmpArray = []
    for reaction in reactions:
        tmp = reaction.strip().split(' ')
        for key in [x for x in dictionary if x in tmp[-1]]:
            tmp[-1] = re.sub(r'(\W|^){0}(\W|$)'.format(key),r'\1{0}\2'.format(dictionary[key]),tmp[-1])
        tmpArray.append(' '.join(tmp))
    tmpArray.append(' '.join(tmp))
    return tmpArray
    
def unrollFunctions(functions):
    flag = True
    #bngl doesnt accept nested function calling
    while(flag):
        dictionary = OrderedDict()
        flag = False
        for function in functions:
            tmp = function.split(' = ')
            for key in dictionary:
                if key in tmp[1]:
                    tmp[1] = re.sub(r'(\W|^){0}\(\)(\W|$)'.format(key),r'\1({0})\2'.format(dictionary[key]),tmp[1])
                    flag = False
            dictionary[tmp[0].split('()')[0]] = tmp[1]
        tmp = []
        for key in dictionary:
            tmp.append('{0} = {1}'.format(key,dictionary[key]))
        functions = tmp
    return functions
            
        
            
    
def analyzeHelper(document,reactionDefinitions,useID,outputFile,speciesEquivalence,atomize,translator):
    '''
    taking the atomized dictionary and a series of data structure, this method
    does the actual string output.
    '''
    useArtificialRules = False
    parser =SBML2BNGL(document.getModel(),useID)
    database = structures.Databases()
    #translator,log,rdf = m2c.transformMolecules(parser,database,reactionDefinitions,speciesEquivalence)
        
    #try:
    if atomize:
        translator = mc.transformMolecules(parser,database,reactionDefinitions,speciesEquivalence)
    else:    
        translator={} 
    
    parser =SBML2BNGL(document.getModel(),useID)
    #except:
    #    print 'failure'
    #    return None,None,None,None
    
    #translator = {}
    param,zparam = parser.getParameters()
    molecules,initialConditions,observables = parser.getSpecies(translator,[x.split(' ')[0] for x in param])
    compartments = parser.getCompartments()
    functions = []
    assigmentRuleDefinedParameters = []
    reactionParameters,rules,rateFunctions = parser.getReactions(translator,len(compartments)>1)
    functions.extend(rateFunctions)
    aParameters,aRules,nonzparam,artificialRules,removeParams,artificialObservables = parser.getAssignmentRules(zparam,param,molecules)
    for element in nonzparam:
        param.append('{0} 0'.format(element))
    param = [x for x in param if x not in removeParams]
    tags = '@{0}'.format(compartments[0].split(' ')[0]) if len(compartments) == 1 else '@cell'
    molecules.extend([x.split(' ')[0] for x in removeParams])
    if len(molecules) == 0:
        compartments = []
    observables.extend('Species {0} {0}'.format(x.split(' ')[0]) for x in removeParams)
    for x in removeParams:
        initialConditions.append(x.split(' ')[0] + tags + ' ' + x.split(' ')[1])

    ##Comment out those parameters that are defined with assignment rules
    ##TODO: I think this is correct, but it may need to be checked
    tmpParams = []
    for idx,parameter in enumerate(param):
        for key in artificialObservables:
            
            if re.search('^{0}\s'.format(key),parameter)!= None:
                assigmentRuleDefinedParameters.append(idx)
    tmpParams.extend(artificialObservables)
    tmpParams.extend(removeParams)
    tmpParams = set(tmpParams)
    correctRulesWithParenthesis(rules,tmpParams)
    for element in assigmentRuleDefinedParameters:
        param[element] = '#' + param[element]
        
    
    deleteMolecules = []
    deleteMoleculesFlag = True 
    for key in artificialObservables:
        flag = -1
        for idx,observable in enumerate(observables):
            if 'Species {0} {0}()'.format(key) in observable:
                flag = idx
        if flag != -1:
            observables.pop(flag)
        functions.append(artificialObservables[key])
        flag = -1
        
        if '{0}()'.format(key) in molecules:
            flag = molecules.index('{0}()'.format(key))
        
        if flag != -1:
            if deleteMoleculesFlag:
                deleteMolecules.append(flag)
            else:
                deleteMolecules.append(key)
            #result =validateReactionUsage(molecules[flag],rules)
            #if result != None:
            #    logMess('ERROR','Pseudo observable {0} in reaction {1}'.format(molecules[flag],result))
            #molecules.pop(flag)
            
        flag = -1
        for idx,specie in enumerate(initialConditions):
            if ':{0}('.format(key) in specie:
                flag = idx
        if flag != -1:
            initialConditions[flag] = '#' + initialConditions[flag]
    
    for flag in sorted(deleteMolecules,reverse=True):
        
        if deleteMoleculesFlag:
            logMess('WARNING','{0} reported as function, but usage is ambiguous'.format(molecules[flag]) )
            result =validateReactionUsage(molecules[flag],rules)
            if result != None:
                logMess('ERROR','Pseudo observable {0} in reaction {1}'.format(molecules[flag],result))
            molecules.pop(flag)
        else:
            logMess('WARNING','{0} reported as species, but usage is ambiguous.'.format(flag) )
            artificialObservables.pop(flag)
    functions.extend(aRules)
    sbmlfunctions = parser.getSBMLFunctions()
    
    processFunctions(functions,sbmlfunctions,artificialObservables,rateFunctions)
    
    for interation in range(0,3):
        for sbml2 in sbmlfunctions:
            for sbml in sbmlfunctions:
                if sbml == sbml2:
                    continue
                if sbml in sbmlfunctions[sbml2]:
                    sbmlfunctions[sbml2] = writer.extendFunction(sbmlfunctions[sbml2],sbml,sbmlfunctions[sbml])
    functions = reorderFunctions(functions)
    functions = changeNames(functions,aParameters)
    
    functions = unrollFunctions(functions)
    rules = changeRates(rules,aParameters)

    if len(compartments) > 1 and 'cell 3 1.0' not in compartments:
        compartments.append('cell 3 1.0')

    #sbml always has the 'cell' default compartment, even when it
    #doesn't declare it
    elif len(compartments) == 0 and len(molecules) != 0:
        compartments.append('cell 3 1.0')
    
    
    if len(artificialRules) + len(rules) == 0:
        logMess('ERROR','The file contains no reactions')
    if useArtificialRules or len(rules) == 0:
        rules =['#{0}'.format(x) for x in rules]
        evaluate =  evaluation(len(observables),translator)

        artificialRules.extend(rules)
        finalString = writer.finalText(param,molecules,initialConditions,set(observables),set(artificialRules),functions,compartments,outputFile)
        


    else:
        artificialRules =['#{0}'.format(x) for x in artificialRules]
        evaluate =  evaluation(len(observables),translator)

        rules.extend(artificialRules)
        
        finalString = writer.finalText(param+reactionParameters,molecules,initialConditions,set(observables),set(rules),functions,compartments,outputFile)
    print outputFile
    
    #store a logfile
    
    if len(logMess.log) > 0:
        with open(outputFile + '.log', 'w') as f:
            for element in logMess.log:
                f.write(element + '\n')
    

    #rate of each classified rule

    return len(rules),evaluate,len(molecules)*1.0/len(observables),len(compartments), parser.getSpeciesAnnotation(),finalString
    
    '''
    if translator != {}:
        for element in database.classifications:
            if element not in classificationDict:
                classificationDict[element] = 0.0
            classificationDict[element] += 1.0/len(database.classifications)
        return len(rules), evaluate,parser.getModelAnnotation(),classificationDict
    '''
    #return None,None,None,None

def processFile(translator, parser, outputFile):
    param2 = parser.getParameters()
    molecules, species, observables = parser.getSpecies(translator)
    compartments = parser.getCompartments()
    param, rules, functions = parser.getReactions(translator, True)
    param += param2
    writer.finalText(param, molecules, species, observables, rules,
                     functions, compartments, outputFile)

   
def BNGL2XML():
    pass

def getAnnotations(annotation):
    annotationDictionary = []
    if annotation == [] or annotation is None:
        return []
    for index in range(0, annotation.getNumAttributes()):
        annotationDictionary.append(annotation.getValue(index))
    return annotationDictionary

def getAnnotationsDict(annotation):
    annotationDict = {}
    for element in annotation:
        annotationDict[element] = getAnnotations(annotation[element])
    return annotationDict

def processFile2():
    for bioNumber in [338]:
        #if bioNumber in [398]:
        #    continue
    #bioNumber = 175
        logMess.log = []
        logMess.counter = -1
        reactionDefinitions,useID = selectReactionDefinitions('BIOMD%010i.xml' %bioNumber)
        print reactionDefinitions, useID
        #reactionDefinitions = 'reactionDefinitions/reactionDefinition7.json'
        #spEquivalence = 'reactionDefinitions/speciesEquivalence19.json'
        spEquivalence = detectCustomDefinitions(bioNumber)
        print spEquivalence
        useID = False
        #reactionDefinitions = 'reactionDefinitions/reactionDefinition9.json'
        outputFile = 'raw/output' + str(bioNumber) + '.bngl'
        analyzeFile('XMLExamples/curated/BIOMD%010i.xml' % bioNumber, reactionDefinitions,
                    useID,outputFile,speciesEquivalence=spEquivalence,atomize=False)

        if len(logMess.log) > 0:
            with open(outputFile + '.log', 'w') as f:
                for element in logMess.log:
                    f.write(element + '\n')

def detectCustomDefinitions(bioNumber):
    '''
    returns a speciesDefinition<bioNumber>.json fileName if it exist
    for the current bioModels. None otherwise
    '''
    directory = 'reactionDefinitions'
    onlyfiles = [ f for f in listdir('./' + directory)]
    if 'speciesEquivalence{0}.json'.format(bioNumber) in onlyfiles:
        return '{0}/speciesEquivalence{1}.json'.format(directory,bioNumber)
    return None


def main():
    jsonFiles = [ f for f in listdir('./reactionDefinitions') if f[-4:-1] == 'jso']
    jsonFiles.sort()
    parser = OptionParser()
    rulesLength = []
    evaluation = []
    evaluation2 = []
    compartmentLength = []
    parser.add_option("-i","--input",dest="input",
        default='XMLExamples/curated/BIOMD0000000272.xml',type="string",
        help="The input SBML file in xml format. Default = 'input.xml'",metavar="FILE")
    parser.add_option("-o","--output",dest="output",
        default='output.bngl',type="string",    
        help="the output file where we will store our matrix. Default = output.bngl",metavar="FILE")

    (options, _) = parser.parse_args()
    #144
    rdfArray = []
    #classificationArray = []
    #18,32,87,88,91,109,253,255,268,338,330
    #normal:51,353
    #cycles 18,108,109,255,268,392
    for bioNumber in range(1,463):
        #if bioNumber in [18,51,353,108,109,255,268,392]:
        #    continue
    #bioNumber = 175
        logMess.log = []
        logMess.counter = -1
        reactionDefinitions,useID = selectReactionDefinitions('BIOMD%010i.xml' %bioNumber)
        print reactionDefinitions, useID
        #reactionDefinitions = 'reactionDefinitions/reactionDefinition7.json'
        #spEquivalence = 'reactionDefinitions/speciesEquivalence19.json'
        spEquivalence = detectCustomDefinitions(bioNumber)
        #reactionDefinitions = 'reactionDefinitions/reactionDefinition8.json'
        #rlength, reval, reval2, clength,rdf = analyzeFile('XMLExamples/curated/BIOMD%010i.xml' % bioNumber, 
        #                                                  reactionDefinitions,False,'complex/output' + str(bioNumber) + '.bngl',
        #                                                    speciesEquivalence=spEquivalence,atomize=True)

        try:
            rlength, reval, reval2, clength,rdf = analyzeFile('XMLExamples/curated/BIOMD%010i.xml' % bioNumber, 
                                                              reactionDefinitions,False,'raw/output' + str(bioNumber) + '.bngl',
                                                                speciesEquivalence=spEquivalence,atomize=False)
        except:
            print '-------------error--------------'
            rulesLength.append(-1)
            continue
            
        if rlength != None:        
            rulesLength.append(rlength)
            evaluation.append(reval)
            evaluation2.append(reval2)
            compartmentLength.append(clength)
            rdfArray.append(getAnnotationsDict(rdf))
        
        else:
            rulesLength.append(-1)
            evaluation.append(0)
            evaluation2.append(0)
            compartmentLength.append(0)
            rdfArray.append({})
            #classificationArray.append({})
    #print evaluation
    #print evaluation2
    #sortedCurated = [i for i in enumerate(evaluation), key=lambda x:x[1]]
    print [(idx+1,x) for idx,x in enumerate(rulesLength) if  x > 50]
    with open('sortedC.dump','wb') as f:
        pickle.dump(rulesLength,f)
        pickle.dump(evaluation,f)
        pickle.dump(evaluation2,f)   
    with open('annotations.dump','wb') as f:
        pickle.dump(rdfArray,f)
    #with open('classificationDict.dump','wb') as f:
    #    pickle.dump(classificationArray,f)
    '''
    plt.hist(rulesLength,bins=[10,30,50,70,90,110,140,180,250,400])
    plt.xlabel('Number of reactions',fontsize=18)
    plt.savefig('lengthDistro.png')
    plt.clf()
    plt.hist(evaluation, bins=[0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7,
                                0.8, 0.9, 1.0])
    plt.xlabel('Atomization Degree',fontsize=18)    
    plt.savefig('ruleifyDistro.png')
    plt.clf()
    plt.hist(evaluation2, bins=[0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7,
                                0.8, 0.9, 1.0])
    plt.xlabel('Atomization Degree', fontsize=18)
    plt.savefig('ruleifyDistro2.png')
    plt.clf()
    ev = []
    idx = 1
    for x, y, z in zip(rulesLength, evaluation, compartmentLength):
        
        if idx in [18, 51, 353, 108, 109, 255, 268, 392]:
            idx+=1

        if x < 15 and y > 0.7 and z>1:
            print '---',idx,x,y
        idx+=1
    #plt.hist(ev,bins =[0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0])
    #plt.xlabel('Atomization Degree',fontsize=18)    
    #plt.savefig('ruleifyDistro3.png')
    '''
            
def main2():
    with open('XMLExamples/curated/BIOMD0000000001.xml','r') as f:
        st = f.read()
        print readFromString(st,
              'reactionDefinitions/reactionDefinition9.json',True,None,True)        



def isActivated(statusVector):
    if statusVector[0] != '' or statusVector[1] not in ['','U','0']:
        return True
    return False
    

def flatStatusVector(statusVector):
    if statusVector[0] != '':
        return '!'
    return statusVector[1]
 
def xorBox(status1,status2):
    return not(status1 & status2)
    
def orBox(status1,status2):
    return (status1,status2)
    
def totalEnumerations(pairList):
    xCoordinate = set()
    yCoordinate = set()
    for element in pairList:
        xCoordinate.add(element[0])
        yCoordinate.add(element[1])
    xCoordinate = list(xCoordinate)
    yCoordinate = list(yCoordinate)
    matrix = np.zeros((len(xCoordinate),len(yCoordinate)))
    for element in pairList:
        matrix[xCoordinate.index(element[0])][yCoordinate.index(element[1])] = 1
    return np.all(np.all(matrix))
   
def getRelationshipDegree(componentPair,statusQueryFunction,comparisonFunction,finalComparison):
    componentPairRelationshipDict = {}    
    for pair in componentPair:
        stats = []
        for state in componentPair[pair]:
            status1 = statusQueryFunction(state[0])
            status2 = statusQueryFunction(state[1])
            comparison = comparisonFunction(status1,status2)
            stats.append(comparison)
        if finalComparison(stats):
            print pair,componentPair[pair]
        componentPairRelationshipDict[pair] = finalComparison(stats)
    return componentPairRelationshipDict

def createPlot(labelDict):
    #f, ax = plt.subplots(int(math.ceil(len(labelDict)/4)),4)
    for idx,element in enumerate(labelDict):
        plt.cla()
        tmp = list(set([y for x in labelDict[element] for y in x]))
        xaxis = [tmp.index(x[0]) for x in labelDict[element] if  labelDict[element][x]== True]
        yaxis = [tmp.index(x[1]) for x in labelDict[element] if labelDict[element][x] == True]
        #6print tmp,xaxis,yaxis
        plt.scatter(xaxis,yaxis)
        plt.xticks(range(len(tmp)),tmp)
        plt.yticks(range(len(tmp)),tmp)
        plt.title(element)
        #ax[math.floor(idx/4)][idx%4].scatter(xaxis,yaxis)
        #ax[math.floor(idx/4)][idx%4].xticks(range(len(tmp)),tmp)
        #ax[math.floor(idx/4)][idx%4].yticks(range(len(tmp)),tmp)
        #ax[math.floor(idx/4)][idx%4].title(element)
        plt.savefig('{0}.png'.format(element))
        print '{0}.png'.format(element)
def statFiles():
    
    for bioNumber in [406]:
        reactionDefinitions,useID = selectReactionDefinitions('BIOMD%010i.xml' %bioNumber)
        #speciesEquivalence = None
        speciesEquivalence = 'reactionDefinitions/speciesEquivalence19.json'
                
        componentPairs =  extractCompartmentStatistics('XMLExamples/curated/BIOMD%010i.xml' % bioNumber,useID,reactionDefinitions,speciesEquivalence)
        #analyze the relationship degree betweeen the components of each molecule
        
        #in this case we are analyzing for orBoxes, or components
        #that completely exclude each other
        xorBoxDict = {}
        orBoxDict = {}
        for molecule in componentPairs:
            xorBoxDict[molecule] = getRelationshipDegree(componentPairs[molecule],isActivated,xorBox,all)
            #print '----------------------',molecule,'---------'            
            orBoxDict[molecule] =  getRelationshipDegree(componentPairs[molecule],flatStatusVector,orBox,totalEnumerations)

        #createPlot(orBoxDict)
        box = []
        box.append(xorBoxDict)
        #box.append(orBoxDict)
        with open('orBox{0}.dump'.format(bioNumber),'wb') as f:
            pickle.dump(box,f)

def processFile3(fileName,customDefinitions=None,atomize=True):
    '''
    processes a file. derp.
    '''
    logMess.log = []
    logMess.counter = -1
    reactionDefinitions = 'reactionDefinitions/reactionDefinition7.json'
    spEquivalence = customDefinitions
    #spEquivalence = None
    useID = False
    #reactionDefinitions = 'reactionDefinitions/reactionDefinition9.json'
    outputFile = '{0}.bngl'.format(fileName)
    analyzeFile(fileName, reactionDefinitions,
                useID,outputFile,speciesEquivalence=spEquivalence,atomize=atomize)

    if len(logMess.log) > 0:
        with open(fileName + '.log', 'w') as f:
            for element in logMess.log:
                f.write(element + '\n')
    
    
def listFiles(minReactions,directory):
    '''
    List of SBML files that meet a given condition
    '''
    from os import listdir
    from os.path import isfile, join
    
    xmlFiles = [ f for f in listdir('./' + directory) if isfile(join('./' + directory,f)) and 'xml' in f]
    outputList = []
    for xml in xmlFiles:
        print '.',
        reader = libsbml.SBMLReader()
        document = reader.readSBMLFromFile(directory + xml)
        model = document.getModel()
        if model == None:
            continue
        if len(model.getListOfReactions()) > minReactions:
            outputList.append(xml)
    print outputList
    print len(outputList)
    
if __name__ == "__main__":
    #identifyNamingConvention()
    #processDatabase()
    #main()
    #processFile3('XMLExamples/curated/BIOMD0000000183.xml')
    #statFiles()
    #main2()
    processFile2()
    #listFiles(50,'./XMLExamples/curated/')
#todo: some of the assignmentRules defined must be used instead of parameters. remove from the paraemter
#definitions those that are defined as 0'
#2:figure out which assignment rules are being used in reactions. Done before the substitution for id;s
#http://nullege.com/codes/show/src@s@e@semanticsbml-HEAD@semanticSBML@annotate.py
#http://wiki.geneontology.org/index.php/Example_Queries#Find_terms_by_GO_ID
#http://www.geneontology.org/GO.database.shtml  