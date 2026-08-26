pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: root
    objectName: "liveDraftView"

    required property var sessionState
    required property var recommendationModel
    required property bool narrow
    required property var displayPreferences

    // The wide recommendation list needs this content width for its fixed
    // metric columns and a two-line card name plus metadata.
    readonly property int wideRecommendationsMinimumWidth: 762 + 450 + Theme.gutter
    readonly property bool wideRecommendations: !root.narrow
        && root.width >= root.wideRecommendationsMinimumWidth

    readonly property bool hasRecommendations: sessionState.recommendations
        && sessionState.recommendations.cards
        && sessionState.recommendations.cards.length > 0
    readonly property bool hasSetupGuidance: Boolean(
        sessionState.status && sessionState.status.setup_guidance
    )
    readonly property string confidenceSummary: {
        const recommendations = sessionState.recommendations
        return recommendations && recommendations.confidence_summary
            ? String(recommendations.confidence_summary) : ""
    }

    readonly property string draftHeading: {
        const draft = sessionState.draft
        if (!draft) {
            if (root.hasSetupGuidance)
                return "Arena setup needed"
            return sessionState.status.message
        }
        if (draft.completed)
            return "Draft complete"
        if (draft.pack_number === null || draft.pick_number === null)
            return draft.event_name
        return "Pack " + (draft.pack_number + 1)
            + " · Pick " + (draft.pick_number + 1)
    }
    readonly property string emptyHeading: {
        if (sessionState.draft && sessionState.draft.completed)
            return "Draft complete"
        if (sessionState.status.phase === "starting")
            return "Loading live draft data"
        if (root.hasSetupGuidance)
            return "Arena setup needed"
        return "Ready for your next Quick Draft"
    }
    readonly property var selectedRecommendation: {
        const recommendations = sessionState.recommendations
        if (!recommendations || !recommendations.cards)
            return null
        const cards = recommendations.cards
        for (let index = 0; index < cards.length; index++) {
            const recommendation = cards[index]
            if (recommendation.card.grp_id === recommendations.selected_grp_id)
                return recommendation
        }
        return cards.length > 0 ? cards[0] : null
    }

    property bool recommendationFocusPublishedWhileVisible: false

    onVisibleChanged: {
        if (!root.visible) {
            root.recommendationFocusPublishedWhileVisible = false
            return
        }
        Qt.callLater(function() {
            if (!root.visible || root.recommendationFocusPublishedWhileVisible
                    || !root.selectedRecommendation) {
                return
            }
            root.recommendationFocusPublishedWhileVisible = true
            sessionProvider.chooseRecommendation(
                root.selectedRecommendation.card.grp_id
            )
        })
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gutter

        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                Label {
                    text: root.draftHeading
                    color: Theme.text
                    font.pixelSize: 22
                    font.bold: true
                }

                Label {
                    text: root.hasRecommendations
                        ? root.sessionState.recommendations.cards.length
                            + " cards available"
                        : root.sessionState.status.message
                    color: Theme.textMuted
                }
            }

            ColumnLayout {
                visible: root.hasRecommendations
                Layout.alignment: Qt.AlignVCenter
                spacing: 2

                Label {
                    objectName: "recommendationConfidenceSummary"
                    visible: root.confidenceSummary.length > 0
                    text: root.confidenceSummary
                    color: Theme.text
                    font.pixelSize: 12
                    font.bold: true
                    wrapMode: Text.WordWrap
                }

                Label {
                    text: root.sessionState.status.message
                    color: Theme.textMuted
                    font.pixelSize: 12
                }
            }

            ComboBox {
                id: rankingSelector
                objectName: "rankingSelector"
                Layout.preferredWidth: root.narrow ? 138 : 166
                model: [
                    { key: "score", label: "DO Score" },
                    { key: "win_rate", label: "17L WR" },
                    { key: "alsa", label: "ALSA" },
                    { key: "mana_value", label: "Mana value" }
                ]
                textRole: "label"
                currentIndex: {
                    const recommendations = root.sessionState.recommendations
                    if (!recommendations)
                        return 0
                    for (let index = 0; index < model.length; index++)
                        if (model[index].key === recommendations.ranking_mode)
                            return index
                    return 0
                }
                Accessible.name: "Recommendation ranking"
                Accessible.description: "Choose DO Score, 17L WR, ALSA, or mana value."
                onActivated: sessionProvider.changeRanking(model[currentIndex].key)
            }
        }

        StateBanner {
            Layout.fillWidth: true
            sessionState: root.sessionState
        }

        Rectangle {
            visible: !root.hasRecommendations
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.surfaceLow
            border.color: Theme.outline
            border.width: 1
            radius: Theme.radius

            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(parent.width - 48, 520)
                spacing: 14

                Label {
                    objectName: "preDraftHeading"
                    Layout.fillWidth: true
                    text: root.emptyHeading
                    color: Theme.text
                    font.pixelSize: 22
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                }

                Label {
                    objectName: "preDraftGuidance"
                    Layout.fillWidth: true
                    text: root.hasSetupGuidance
                        ? root.sessionState.status.message
                        : "Draft Omen follows Arena automatically and never writes to the game."
                    color: root.hasSetupGuidance ? Theme.text : Theme.textMuted
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                Repeater {
                    model: [
                        ["Card metadata", root.sessionState.card_data.message],
                        ["Arena log", root.sessionState.status.message],
                        ["Arena account", root.sessionState.active_account
                            ? root.sessionState.active_account.screen_name
                                || root.sessionState.active_account.account_id
                            : "Not detected"],
                        ["Draft", root.sessionState.draft
                            ? root.sessionState.draft.event_name
                            : "No Quick Draft detected"],
                        ["Ratings", root.sessionState.ratings.message]
                    ]

                    delegate: Rectangle {
                        required property var modelData

                        Layout.fillWidth: true
                        Layout.preferredHeight: 46
                        color: Theme.surface
                        radius: Theme.radius

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12

                            Label {
                                Layout.preferredWidth: 126
                                text: modelData[0]
                                color: Theme.text
                                font.bold: true
                            }
                            Label {
                                Layout.fillWidth: true
                                text: modelData[1]
                                color: Theme.textMuted
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
            }
        }

        RowLayout {
            visible: root.wideRecommendations && root.hasRecommendations
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.gutter

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 500
                color: "transparent"

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.leftMargin: 14
                        Layout.rightMargin: 12
                        spacing: 8

                        Label {
                            objectName: "recommendationHeaderRank"
                            Layout.preferredWidth: 30
                            text: "#"
                            color: Theme.textMuted
                            font.pixelSize: 11
                        }
                        Label {
                            objectName: "recommendationHeaderCard"
                            Layout.fillWidth: true
                            text: "CARD"
                            color: Theme.textMuted
                            font.pixelSize: 11
                        }
                        Label { Layout.preferredWidth: 70; text: "COLORS"; color: Theme.textMuted; font.pixelSize: 11; horizontalAlignment: Text.AlignHCenter }
                        Label { Layout.preferredWidth: 58; text: "DO"; color: Theme.textMuted; font.pixelSize: 11; horizontalAlignment: Text.AlignRight }
                        Label { Layout.preferredWidth: 68; text: "17L WR"; color: Theme.textMuted; font.pixelSize: 11; horizontalAlignment: Text.AlignRight }
                        Label { Layout.preferredWidth: 44; text: "GRADE"; color: Theme.textMuted; font.pixelSize: 11; horizontalAlignment: Text.AlignHCenter }
                        Label { Layout.preferredWidth: 82; text: "FIT"; color: Theme.textMuted; font.pixelSize: 11; horizontalAlignment: Text.AlignRight }
                    }

                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        delegate: RecommendationRow {
                            required property var modelData
                            objectName: "wideRecommendationRow" + modelData.rank

                            width: ListView.view.width
                            recommendation: modelData
                            selected: root.sessionState.recommendations.selected_grp_id
                                === modelData.card.grp_id
                            wide: root.wideRecommendations
                            secondaryStats: root.displayPreferences.secondaryStats
                            onChosen: grpId => sessionProvider.chooseRecommendation(grpId)
                        }
                        clip: true
                        model: root.wideRecommendations
                            ? root.recommendationModel : null
                        Accessible.name: "Ranked recommendations"

                    }
                }
            }

            ColumnLayout {
                Layout.preferredWidth: 450
                Layout.maximumWidth: 450
                Layout.fillHeight: true
                Layout.minimumWidth: 350
                spacing: Theme.gutter

                CardPreview {
                    objectName: "wideLiveCardPreview"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 380
                    Layout.minimumHeight: 350
                    recommendation: root.selectedRecommendation
                    detailedIntel: true
                    loading: root.sessionState.card_data.phase === "loading"
                    imageState: root.sessionState.card_image
                    constrainImageFrameToHeight: true
                }

                PoolSummaryPanel {
                    objectName: "wideLivePoolDetails"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 180
                    pool: root.sessionState.pool
                    narrow: root.narrow
                    onPreviewRequested: grpId => sessionProvider.previewRecentPick(grpId)
                    onPreviewDismissed: sessionProvider.dismissRecentPickPreview()
                }
            }
        }

    ColumnLayout {
        visible: !root.wideRecommendations && root.hasRecommendations
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: 8

        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 220
            spacing: root.displayPreferences.compactDensity ? 3 : 6
            clip: true
            model: root.wideRecommendations ? null : root.recommendationModel
            Accessible.name: "Ranked recommendations"

            delegate: RecommendationRow {
                required property var modelData
                objectName: "narrowRecommendationRow" + modelData.rank

                width: ListView.view.width
                recommendation: modelData
                selected: root.sessionState.recommendations.selected_grp_id
                    === modelData.card.grp_id
                wide: false
                secondaryStats: root.displayPreferences.secondaryStats
                onChosen: grpId => sessionProvider.chooseRecommendation(grpId)
            }
        }

        TabBar {
            id: detailTabs
            objectName: "liveDetailTabs"
            Layout.fillWidth: true
            currentIndex: 0
            Accessible.name: "Live draft details"

            TabButton {
                objectName: "liveCardDetailsTab"
                text: "Card details"
                Accessible.name: "Card details"
            }
            TabButton {
                objectName: "livePoolTab"
                text: "Pool"
                Accessible.name: "Pool details"
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 340
            currentIndex: detailTabs.currentIndex

            CardPreview {
                objectName: "narrowLiveCardPreview"
                recommendation: root.selectedRecommendation
                detailedIntel: true
                loading: root.sessionState.card_data.phase === "loading"
                imageState: root.sessionState.card_image
                constrainImageFrameToHeight: true
            }

            PoolSummaryPanel {
                objectName: "narrowLivePoolDetails"
                pool: root.sessionState.pool
                narrow: root.narrow
                onPreviewRequested: grpId => sessionProvider.previewRecentPick(grpId)
                onPreviewDismissed: sessionProvider.dismissRecentPickPreview()
            }
        }
    }
    }
}

